import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { matchesLooseSearch } from "@/lib/search-normalize";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Ban, Check, Download, Upload } from "lucide-react";
import { ThemedNumberInput } from "@/components/ui/themed-number-input";
import { useLocalFeature } from "@/hooks/sync/use-local-feature";
import { apiUrl } from "@/lib/api";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import { EQUIPMENT_CATALOG, EQUIPMENT_EXCHANGE_ROWS } from "@/lib/generated-equipment-data";

type KairoEquipmentName =
  | "A/ Kairo Sword"
  | "A/ Kairo Hammer"
  | "A/ Kairo Lance"
  | "A/ Kairo Bow"
  | "A/ Kairo Gun";

type SmithyBand = "f_to_c" | "b_or_higher";
type RequirementMode = "auto" | "manual";

type EquipmentCatalogItem = (typeof EQUIPMENT_CATALOG)[number];
type ExchangeRow = (typeof EQUIPMENT_EXCHANGE_ROWS)[number];
type SharedData = { equipIcons?: Record<string, string> };

const KAIRO_OUTPUTS: KairoEquipmentName[] = [
  "A/ Kairo Sword",
  "A/ Kairo Hammer",
  "A/ Kairo Lance",
  "A/ Kairo Bow",
  "A/ Kairo Gun",
];

const SMITHY_LEVEL_ROWS = [
  { level: 5, f_to_c: { kairo: 1, diamond: 10 }, b_or_higher: { kairo: 1, diamond: 100 } },
  { level: 10, f_to_c: { kairo: 1, diamond: 30 }, b_or_higher: { kairo: 1, diamond: 150 } },
  { level: 20, f_to_c: { kairo: 1, diamond: 50 }, b_or_higher: { kairo: 2, diamond: 200 } },
  { level: 30, f_to_c: { kairo: 1, diamond: 75 }, b_or_higher: { kairo: 2, diamond: 250 } },
  { level: 40, f_to_c: { kairo: 1, diamond: 100 }, b_or_higher: { kairo: 3, diamond: 300 } },
  { level: 50, f_to_c: { kairo: 1, diamond: 125 }, b_or_higher: { kairo: 3, diamond: 400 } },
  { level: 60, f_to_c: { kairo: 1, diamond: 150 }, b_or_higher: { kairo: 4, diamond: 500 } },
  { level: 70, f_to_c: { kairo: 1, diamond: 200 }, b_or_higher: { kairo: 4, diamond: 600 } },
  { level: 80, f_to_c: { kairo: 1, diamond: 300 }, b_or_higher: { kairo: 5, diamond: 700 } },
  { level: 90, f_to_c: { kairo: 2, diamond: 400 }, b_or_higher: { kairo: 5, diamond: 800 } },
] as const;

const BREAK_LEVELS = SMITHY_LEVEL_ROWS.map((row) => row.level);
const RANK_TOGGLES = ["F", "E", "D", "C", "B", "A", "S"] as const;
const STORAGE_KEY = "ka_equipment_exchange_state";

interface EquipmentExchangeSavedState {
  target: KairoEquipmentName;
  targetCount: number;
  requirementMode: RequirementMode;
  ownedKairo: number;
  equipmentQuery: string;
  selectedEquipmentId: number | "";
  currentLevel: number;
  targetLevel: number;
  sourceQuery: string;
  sourceRankFilters: string[];
  sourceGivesFilters: string[];
  currentPriceInputs: Record<number, string>;
  unavailableSourceIds: number[];
  funFactRanks: string[];
}

const DEFAULT_EQUIPMENT_EXCHANGE_STATE: EquipmentExchangeSavedState = {
  target: "A/ Kairo Gun",
  targetCount: 10,
  requirementMode: "auto",
  ownedKairo: 0,
  equipmentQuery: "",
  selectedEquipmentId: "",
  currentLevel: 1,
  targetLevel: 99,
  sourceQuery: "",
  sourceRankFilters: [],
  sourceGivesFilters: [],
  currentPriceInputs: {},
  unavailableSourceIds: [],
  funFactRanks: [...RANK_TOGGLES],
};

const PLAYER_EQUIPMENT = (EQUIPMENT_CATALOG as readonly EquipmentCatalogItem[]).filter((item) =>
  /^[FSABCDEG]\s*\//i.test(item.name),
);

function isValidTradeValue(value: number, start: number, step: number) {
  if (!Number.isFinite(value) || value < start) return false;
  return (value - start) % step === 0;
}

function formatRankBand(rank: string) {
  return ["F", "E", "D", "C"].includes(rank) ? "Class F to C" : "B or higher";
}

function smithyBandForRank(rank: string): SmithyBand {
  return ["F", "E", "D", "C"].includes(rank) ? "f_to_c" : "b_or_higher";
}

function getSmithyRequirement(levels: number[], band: SmithyBand) {
  return levels.reduce(
    (acc, level) => {
      const row = SMITHY_LEVEL_ROWS.find((item) => item.level === level);
      if (!row) return acc;
      acc.kairo += row[band].kairo;
      acc.diamond += row[band].diamond;
      return acc;
    },
    { kairo: 0, diamond: 0 },
  );
}

function summarizeKairoCounts(items: EquipmentCatalogItem[], targetLevel = 99) {
  const counts: Record<KairoEquipmentName, number> = {
    "A/ Kairo Sword": 0,
    "A/ Kairo Hammer": 0,
    "A/ Kairo Lance": 0,
    "A/ Kairo Bow": 0,
    "A/ Kairo Gun": 0,
  };
  let diamond = 0;

  for (const item of items) {
    const levels = BREAK_LEVELS.filter((level) => level <= targetLevel);
    const requirement = getSmithyRequirement(levels, smithyBandForRank(item.rankLabel));
    counts[item.requiredKairo as KairoEquipmentName] += requirement.kairo;
    diamond += requirement.diamond;
  }

  return {
    counts,
    totalKairo: Object.values(counts).reduce((sum, value) => sum + value, 0),
    diamond,
    equipmentCount: items.length,
  };
}

function getCheapestCopperBuyForTarget(target: KairoEquipmentName, count: number) {
  const entries = (EQUIPMENT_EXCHANGE_ROWS as readonly ExchangeRow[]).filter(
    (entry) => entry.outputName === target && entry.tradable,
  );
  if (entries.length === 0 || count <= 0) return 0;

  const states = entries.map((entry) => ({
    entry,
    currentExchange: entry.startPrice,
  }));

  let totalBuy = 0;
  for (let i = 0; i < count; i += 1) {
    let best = states[0];
    let bestCombined = Number.POSITIVE_INFINITY;
    for (const state of states) {
      const combined = state.entry.buyPrice * state.currentExchange;
      if (
        combined < bestCombined ||
        (combined === bestCombined && state.currentExchange < best.currentExchange) ||
        (combined === bestCombined &&
          state.currentExchange === best.currentExchange &&
          state.entry.inputName < best.entry.inputName)
      ) {
        best = state;
        bestCombined = combined;
      }
    }
    totalBuy += best.entry.buyPrice * best.currentExchange;
    best.currentExchange += best.entry.priceStep;
  }

  return totalBuy;
}

function summarizeCopperCoinRoute(items: EquipmentCatalogItem[], targetLevel = 99) {
  const summary = summarizeKairoCounts(items, targetLevel);
  return KAIRO_OUTPUTS.reduce((sum, output) => sum + getCheapestCopperBuyForTarget(output, summary.counts[output]), 0);
}

function buildRoute(
  routeEntries: readonly ExchangeRow[],
  parsedCurrentPrices: Record<number, number>,
  targetCount: number,
) {
  const safeTargetCount = Math.max(0, targetCount);
  const states = routeEntries.map((entry) => ({
    entry,
    currentExchange: parsedCurrentPrices[entry.inputId] ?? entry.startPrice,
    used: 0,
    totalBuy: 0,
    totalExchange: 0,
  }));

  const picks: Array<{ item: string; tradeCost: number; buyCost: number; totalCost: number }> = [];
  let totalBuy = 0;
  let totalExchange = 0;

  if (states.length === 0) {
    return {
      totalBuy,
      totalExchange,
      totalCombined: 0,
      usedEntries: [],
      picks,
    };
  }

  for (let i = 0; i < safeTargetCount; i += 1) {
    let best = states[0];
    let bestCombined = Number.POSITIVE_INFINITY;
    for (const state of states) {
      const combined = state.entry.buyPrice * state.currentExchange;
      if (
        combined < bestCombined ||
        (combined === bestCombined && state.currentExchange < best.currentExchange) ||
        (combined === bestCombined &&
          state.currentExchange === best.currentExchange &&
          state.entry.inputName < best.entry.inputName)
      ) {
        best = state;
        bestCombined = combined;
      }
    }

    if (!best) break;

    const tradeCost = best.entry.buyPrice * best.currentExchange;
    best.used += 1;
    best.totalBuy += tradeCost;
    best.totalExchange += best.currentExchange;
    totalBuy += tradeCost;
    totalExchange += best.currentExchange;
    picks.push({
      item: best.entry.inputName,
      tradeCost: best.currentExchange,
      buyCost: best.entry.buyPrice,
      totalCost: tradeCost,
    });
    best.currentExchange += best.entry.priceStep;
  }

  const usedEntries = states
    .filter((state) => state.used > 0)
    .map((state) => ({ ...state, nextExchange: state.currentExchange }))
    .sort((a, b) => {
      if (b.used !== a.used) return b.used - a.used;
      return a.entry.inputName.localeCompare(b.entry.inputName);
    });

  return {
    totalBuy,
    totalExchange,
    totalCombined: totalBuy + totalExchange,
    usedEntries,
    picks,
  };
}

export default function EquipmentExchangeCalculator() {
  const { data: sharedData } = useQuery({
    queryKey: ["ka-shared"],
    queryFn: () => fetchSharedWithFallback<SharedData>(apiUrl("/shared")),
    staleTime: 15000,
  });
  const equipIcons = sharedData?.equipIcons ?? {};
  const [savedExchangeState, setSavedExchangeState] = useLocalFeature<EquipmentExchangeSavedState>(
    STORAGE_KEY,
    DEFAULT_EQUIPMENT_EXCHANGE_STATE,
  );

  const [target, setTarget] = useState<KairoEquipmentName>(savedExchangeState.target ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.target);
  const [targetCount, setTargetCount] = useState(savedExchangeState.targetCount ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.targetCount);
  const [requirementMode, setRequirementMode] = useState<RequirementMode>(savedExchangeState.requirementMode ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.requirementMode);
  const [ownedKairo, setOwnedKairo] = useState(savedExchangeState.ownedKairo ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.ownedKairo);
  const [equipmentQuery, setEquipmentQuery] = useState(savedExchangeState.equipmentQuery ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.equipmentQuery);
  const [selectedEquipmentId, setSelectedEquipmentId] = useState<number | "">(savedExchangeState.selectedEquipmentId ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.selectedEquipmentId);
  const [currentLevel, setCurrentLevel] = useState(savedExchangeState.currentLevel ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.currentLevel);
  const [targetLevel, setTargetLevel] = useState(savedExchangeState.targetLevel ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.targetLevel);
  const [sourceQuery, setSourceQuery] = useState(savedExchangeState.sourceQuery ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.sourceQuery);
  const [sourceRankFilters, setSourceRankFilters] = useState<Set<string>>(new Set(savedExchangeState.sourceRankFilters ?? []));
  const [sourceGivesFilters, setSourceGivesFilters] = useState<Set<string>>(new Set(savedExchangeState.sourceGivesFilters ?? []));
  const [givesDropdownOpen, setGivesDropdownOpen] = useState(false);
  const givesDropdownRef = useRef<HTMLDivElement>(null);
  const [equipmentDropdownOpen, setEquipmentDropdownOpen] = useState(false);
  const equipmentDropdownRef = useRef<HTMLDivElement>(null);
  const [currentPriceInputs, setCurrentPriceInputs] = useState<Record<number, string>>(savedExchangeState.currentPriceInputs ?? {});
  const [unavailableSourceIds, setUnavailableSourceIds] = useState<Set<number>>(new Set(savedExchangeState.unavailableSourceIds ?? []));

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (givesDropdownRef.current && !givesDropdownRef.current.contains(e.target as Node)) {
        setGivesDropdownOpen(false);
      }
      if (equipmentDropdownRef.current && !equipmentDropdownRef.current.contains(e.target as Node)) {
        setEquipmentDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);
  const [funFactRanks, setFunFactRanks] = useState<Set<string>>(new Set(savedExchangeState.funFactRanks ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.funFactRanks));
  const [clipboardStatus, setClipboardStatus] = useState<{ type: "ok" | "error"; message: string } | null>(null);

  const exportPayload = useMemo(
    () => ({
      target,
      targetCount,
      requirementMode,
      ownedKairo,
      equipmentQuery,
      selectedEquipmentId,
      currentLevel,
      targetLevel,
      sourceQuery,
      sourceRankFilters: Array.from(sourceRankFilters),
      sourceGivesFilters: Array.from(sourceGivesFilters),
      currentPriceInputs,
      unavailableSourceIds: Array.from(unavailableSourceIds),
      funFactRanks: Array.from(funFactRanks),
    }),
    [
      currentLevel,
      currentPriceInputs,
      equipmentQuery,
      funFactRanks,
      ownedKairo,
      requirementMode,
      selectedEquipmentId,
      sourceGivesFilters,
      sourceQuery,
      sourceRankFilters,
      target,
      targetCount,
      targetLevel,
      unavailableSourceIds,
    ],
  );

  const exportText = useMemo(() => JSON.stringify(exportPayload), [exportPayload]);

  const copyExportText = async () => {
    try {
      await navigator.clipboard.writeText(exportText);
      setClipboardStatus({ type: "ok", message: "Copied to clipboard" });
    } catch {
      setClipboardStatus({ type: "error", message: "Copy failed" });
    }
  };

  useEffect(() => {
    setSavedExchangeState({
      target,
      targetCount,
      requirementMode,
      ownedKairo,
      equipmentQuery,
      selectedEquipmentId,
      currentLevel,
      targetLevel,
      sourceQuery,
      sourceRankFilters: Array.from(sourceRankFilters),
      sourceGivesFilters: Array.from(sourceGivesFilters),
      currentPriceInputs,
      unavailableSourceIds: Array.from(unavailableSourceIds),
      funFactRanks: Array.from(funFactRanks),
    });
  }, [
    target,
    targetCount,
    requirementMode,
    ownedKairo,
    equipmentQuery,
    selectedEquipmentId,
    currentLevel,
    targetLevel,
    sourceQuery,
    sourceRankFilters,
    sourceGivesFilters,
    currentPriceInputs,
    unavailableSourceIds,
    funFactRanks,
    setSavedExchangeState,
  ]);

  const importExchangeState = (content: string) => {
    try {
      const parsed = JSON.parse(content) as Partial<EquipmentExchangeSavedState>;
      const nextState: EquipmentExchangeSavedState = {
        ...DEFAULT_EQUIPMENT_EXCHANGE_STATE,
        ...parsed,
        sourceRankFilters: parsed.sourceRankFilters ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.sourceRankFilters,
        sourceGivesFilters: parsed.sourceGivesFilters ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.sourceGivesFilters,
        funFactRanks: parsed.funFactRanks ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.funFactRanks,
        currentPriceInputs: parsed.currentPriceInputs ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.currentPriceInputs,
        unavailableSourceIds: parsed.unavailableSourceIds ?? DEFAULT_EQUIPMENT_EXCHANGE_STATE.unavailableSourceIds,
      };
      setSavedExchangeState(nextState);
      setTarget(nextState.target);
      setTargetCount(nextState.targetCount);
      setRequirementMode(nextState.requirementMode);
      setOwnedKairo(nextState.ownedKairo);
      setEquipmentQuery(nextState.equipmentQuery);
      setSelectedEquipmentId(nextState.selectedEquipmentId);
      setCurrentLevel(nextState.currentLevel);
      setTargetLevel(nextState.targetLevel);
      setSourceQuery(nextState.sourceQuery);
      setSourceRankFilters(new Set(nextState.sourceRankFilters));
      setSourceGivesFilters(new Set(nextState.sourceGivesFilters));
      setCurrentPriceInputs(nextState.currentPriceInputs);
      setUnavailableSourceIds(new Set(nextState.unavailableSourceIds));
      setFunFactRanks(new Set(nextState.funFactRanks));
      setClipboardStatus({ type: "ok", message: "Imported from clipboard" });
      return true;
    } catch {
      setClipboardStatus({ type: "error", message: "Import failed" });
      return false;
    }
  };

  const pasteImportText = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (!text.trim()) {
        setClipboardStatus({ type: "error", message: "Clipboard is empty" });
        return;
      }
      importExchangeState(text.trim());
    } catch {
      setClipboardStatus({ type: "error", message: "Paste failed" });
      return;
    }
  };

  const selectedEquipment = useMemo(
    () => PLAYER_EQUIPMENT.find((item) => item.id === selectedEquipmentId) ?? null,
    [selectedEquipmentId],
  );

  useEffect(() => {
    if (!selectedEquipment) return;
    setTarget(selectedEquipment.requiredKairo as KairoEquipmentName);
  }, [selectedEquipment]);

  const equipmentOptions = useMemo(() => {
    const q = equipmentQuery.trim();
    if (!q) return PLAYER_EQUIPMENT;
    return PLAYER_EQUIPMENT.filter((item) => matchesLooseSearch(item.name, q));
  }, [equipmentQuery]);

  const displayedEquipmentId = useMemo(() => {
    if (!equipmentQuery.trim()) return selectedEquipmentId;
    if (selectedEquipmentId !== "" && equipmentOptions.some((item) => item.id === selectedEquipmentId)) {
      return selectedEquipmentId;
    }
    return equipmentOptions[0]?.id ?? "";
  }, [equipmentOptions, equipmentQuery, selectedEquipmentId]);

  useEffect(() => {
    if (!equipmentQuery.trim()) return;
    const firstMatch = equipmentOptions[0];
    if (!firstMatch) return;
    const currentStillVisible = equipmentOptions.some((item) => item.id === selectedEquipmentId);
    if (!currentStillVisible) {
      setSelectedEquipmentId(firstMatch.id);
    }
  }, [equipmentOptions, equipmentQuery, selectedEquipmentId]);

  const selectEquipment = (item: EquipmentCatalogItem) => {
    setSelectedEquipmentId(item.id);
    setEquipmentQuery(item.name);
    setEquipmentDropdownOpen(false);
  };

  const normalizedCurrentLevel = Math.max(1, Math.min(99, currentLevel));
  const normalizedTargetLevel = Math.max(normalizedCurrentLevel, Math.min(99, targetLevel));

  const requiredBreakLevels = useMemo(
    () => BREAK_LEVELS.filter((level) => level >= normalizedCurrentLevel && level < normalizedTargetLevel),
    [normalizedCurrentLevel, normalizedTargetLevel],
  );

  const selectedRequirement = useMemo(() => {
    if (!selectedEquipment) return { kairo: 0, diamond: 0 };
    return getSmithyRequirement(requiredBreakLevels, smithyBandForRank(selectedEquipment.rankLabel));
  }, [requiredBreakLevels, selectedEquipment]);

  const autoNeededKairo = useMemo(
    () => Math.max(0, selectedRequirement.kairo - Math.max(0, ownedKairo)),
    [ownedKairo, selectedRequirement.kairo],
  );

  useEffect(() => {
    if (requirementMode === "auto" && selectedEquipment) {
      setTargetCount(autoNeededKairo);
    }
  }, [autoNeededKairo, requirementMode, selectedEquipment]);

  const entries = useMemo(
    () => (EQUIPMENT_EXCHANGE_ROWS as readonly ExchangeRow[]).filter((entry) => entry.tradable),
    [],
  );

  const routeEntries = useMemo(
    () => entries.filter((entry) => entry.outputName === target),
    [entries, target],
  );

  const availableRouteEntries = useMemo(
    () => routeEntries.filter((entry) => !unavailableSourceIds.has(entry.inputId)),
    [routeEntries, unavailableSourceIds],
  );

  const parsedCurrentPrices = useMemo(() => {
    const parsed: Record<number, number> = {};
    for (const entry of routeEntries) {
      const raw = currentPriceInputs[entry.inputId];
      if (raw == null || raw.trim() === "") continue;
      const next = Number(raw);
      if (isValidTradeValue(next, entry.startPrice, entry.priceStep)) {
        parsed[entry.inputId] = next;
      }
    }
    return parsed;
  }, [currentPriceInputs, routeEntries]);

  const filteredEntries = useMemo(() => {
    let result = entries;
    if (sourceRankFilters.size > 0) result = result.filter((e) => sourceRankFilters.has(e.rankLabel));
    if (sourceGivesFilters.size > 0) result = result.filter((e) => sourceGivesFilters.has(e.outputName));
    const q = sourceQuery.trim();
    if (q) result = result.filter((e) => matchesLooseSearch(e.inputName, q));
    return result;
  }, [entries, sourceQuery, sourceRankFilters, sourceGivesFilters]);

  const routeWithUnavailableIncluded = useMemo(
    () => buildRoute(routeEntries, parsedCurrentPrices, targetCount),
    [routeEntries, parsedCurrentPrices, targetCount],
  );

  const route = useMemo(
    () => buildRoute(availableRouteEntries, parsedCurrentPrices, targetCount),
    [availableRouteEntries, parsedCurrentPrices, targetCount],
  );

  const routeChangesByInputId = useMemo(() => {
    const baseline = new Map(routeWithUnavailableIncluded.usedEntries.map((state) => [state.entry.inputId, state]));
    const changes: Record<number, { previousExchange: number; previousBuy: number }> = {};
    for (const state of route.usedEntries) {
      const previous = baseline.get(state.entry.inputId);
      if (!previous) continue;
      if (previous.totalExchange !== state.totalExchange || previous.totalBuy !== state.totalBuy) {
        changes[state.entry.inputId] = {
          previousExchange: previous.totalExchange,
          previousBuy: previous.totalBuy,
        };
      }
    }
    return changes;
  }, [route.usedEntries, routeWithUnavailableIncluded.usedEntries]);

  const funFactItems = useMemo(
    () => PLAYER_EQUIPMENT.filter((item) => funFactRanks.has(item.rankLabel as (typeof RANK_TOGGLES)[number])),
    [funFactRanks],
  );
  const funFacts = useMemo(() => summarizeKairoCounts(funFactItems), [funFactItems]);
  const allGameFacts = useMemo(() => summarizeKairoCounts(PLAYER_EQUIPMENT), []);
  const allAFacts = useMemo(() => summarizeKairoCounts(PLAYER_EQUIPMENT.filter((item) => item.rankLabel === "A")), []);
  const allSFacts = useMemo(() => summarizeKairoCounts(PLAYER_EQUIPMENT.filter((item) => item.rankLabel === "S")), []);
  const allGameCopper = useMemo(() => summarizeCopperCoinRoute(PLAYER_EQUIPMENT), []);
  const allACopper = useMemo(() => summarizeCopperCoinRoute(PLAYER_EQUIPMENT.filter((item) => item.rankLabel === "A")), []);
  const allSCopper = useMemo(() => summarizeCopperCoinRoute(PLAYER_EQUIPMENT.filter((item) => item.rankLabel === "S")), []);
  const funFactsCopper = useMemo(() => summarizeCopperCoinRoute(funFactItems), [funFactItems]);

  const resetVisible = () => {
    setCurrentPriceInputs((prev) => {
      const next = { ...prev };
      for (const entry of routeEntries) delete next[entry.inputId];
      return next;
    });
  };

  const toggleFunFactRank = (rank: string) => {
    setFunFactRanks((prev) => {
      const next = new Set(prev);
      if (next.has(rank)) next.delete(rank);
      else next.add(rank);
      return next;
    });
  };

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-xl font-bold tracking-tight">Equipment Exchange</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Trade eligible equipment into A-rank Kairo gear. This page uses the corrected mined copper coin buy-price column,
          decoded exchange start prices and steps, and the EN Master Smithy requirement table.
        </p>
      </div>

      <div className="space-y-4 rounded-lg border border-border bg-card p-4">
        <div className="rounded-md border border-border bg-background/50 px-3 py-2 text-xs text-muted-foreground">
          Lower-rank items can be exchanged. Each source item tracks its own live trade price. If you enter a current trade
          value, it must match that item&apos;s valid ladder: start price + (step x number of past trades).
        </div>

        <div className="space-y-3 rounded-md border border-border bg-background/50 p-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-sm font-semibold">Master Smithy helper</h2>
              <p className="text-xs text-muted-foreground">
                Pick the equipment you are leveling, your current level, and your target level. The calculator will figure out
                how many Kairo pieces and diamonds are needed to break the required caps. The highest break point is level 90,
                even though the final item level is 99.
              </p>
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_120px_120px_120px]">
            <div className="space-y-1">
              <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Equipment search</label>
              <div className="relative" ref={equipmentDropdownRef}>
                <Input
                  value={equipmentQuery}
                  onChange={(e) => {
                    setEquipmentQuery(e.target.value);
                    setEquipmentDropdownOpen(true);
                  }}
                  onFocus={() => setEquipmentDropdownOpen(true)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setEquipmentDropdownOpen(false);
                    if (e.key === "Enter" && displayedEquipmentId !== "") {
                      const match = PLAYER_EQUIPMENT.find((item) => item.id === displayedEquipmentId);
                      if (match) {
                        e.preventDefault();
                        selectEquipment(match);
                      }
                    }
                  }}
                  placeholder="Type part of an equipment name"
                  className={`h-9 pr-9 ${equipmentDropdownOpen ? "rounded-b-none border-primary ring-1 ring-primary" : ""}`}
                  role="combobox"
                  aria-expanded={equipmentDropdownOpen}
                  aria-controls="equipment-search-results"
                />
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    setEquipmentDropdownOpen((open) => !open);
                  }}
                  className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded text-primary hover:bg-primary/10"
                  aria-label={equipmentDropdownOpen ? "Close equipment results" : "Open equipment results"}
                >
                  <svg className={`h-4 w-4 transition-transform ${equipmentDropdownOpen ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none">
                    <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {equipmentDropdownOpen && (
                  <div
                    id="equipment-search-results"
                    className="absolute left-0 right-0 top-full z-50 max-h-64 overflow-y-auto rounded-b-md border border-t-0 border-primary bg-popover shadow-lg"
                  >
                    {equipmentOptions.length === 0 ? (
                      <div className="px-3 py-3 text-center text-xs text-muted-foreground">No matching equipment</div>
                    ) : (
                      equipmentOptions.map((item) => {
                        const icon = getEquipmentIcon(equipIcons, item.name);
                        return (
                          <button
                            key={item.id}
                            type="button"
                            onMouseDown={(e) => {
                              e.preventDefault();
                              selectEquipment(item);
                            }}
                            className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted ${
                              item.id === displayedEquipmentId ? "bg-primary/10 font-medium text-foreground" : ""
                            }`}
                          >
                            <span className="flex min-w-0 items-center gap-2">
                              {icon ? <img src={icon} alt="" className="h-5 w-5 shrink-0 rounded object-contain" /> : <span className="h-5 w-5 shrink-0" />}
                              <span className="truncate">{item.name}</span>
                            </span>
                            {item.id === selectedEquipmentId && <Check className="h-3.5 w-3.5 shrink-0 text-primary" />}
                          </button>
                        );
                      })
                    )}
                  </div>
                )}
              </div>
              <div className="text-[10px] text-muted-foreground">
                Search matches partial words in any order, like `swift bow` or `magic staff`.
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Current level</label>
              <ThemedNumberInput value={currentLevel} min={1} max={99} onValueChange={setCurrentLevel} />
            </div>

            <div className="space-y-1">
              <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Target level</label>
              <ThemedNumberInput value={targetLevel} min={1} max={99} onValueChange={setTargetLevel} />
            </div>

            <div className="space-y-1">
              <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Kairo you own</label>
              <ThemedNumberInput value={ownedKairo} min={0} onValueChange={(value) => setOwnedKairo(Math.max(0, value))} />
            </div>
          </div>

          {selectedEquipment && (
            <>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <Card>
                  <CardContent className="p-3">
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Equipment</div>
                    <div className="mt-1 flex items-center gap-2 text-sm font-semibold">
                      {getEquipmentIcon(equipIcons, selectedEquipment.name) && (
                        <img src={getEquipmentIcon(equipIcons, selectedEquipment.name)} alt="" className="h-6 w-6 rounded object-contain" />
                      )}
                      <span>{selectedEquipment.name}</span>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-3">
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Required piece</div>
                    <div className="mt-1 text-sm font-semibold">{selectedEquipment.requiredKairo.replace("A/ ", "")}</div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-3">
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Kairo needed</div>
                    <div className="mt-1 text-lg font-semibold tabular-nums">{autoNeededKairo}</div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-3">
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Diamond cost</div>
                    <div className="mt-1 text-lg font-semibold tabular-nums">{selectedRequirement.diamond}</div>
                  </CardContent>
                </Card>
              </div>

              <div className="rounded-md border border-border bg-background/40 px-3 py-2 text-xs text-muted-foreground">
                Breaks count whenever your current level is still inside that cap. Example: if your item is level 5 and you want
                to go higher, you still need the level 5 break. If you already own some Kairo pieces, they are subtracted here.
              </div>
            </>
          )}
        </div>

        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
          <div className="space-y-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Target Kairo piece</label>
            <div className="flex flex-wrap gap-2">
              {KAIRO_OUTPUTS.map((output) => (
                <button
                  key={output}
                  type="button"
                  onClick={() => setTarget(output)}
                  className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                    target === output ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted"
                  }`}
                >
                  {output}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2 rounded-md border border-border bg-background/50 p-3">
            <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Kairo target mode</div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setRequirementMode("auto")}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
                  requirementMode === "auto" ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted"
                }`}
              >
                Auto
              </button>
              <button
                type="button"
                onClick={() => setRequirementMode("manual")}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
                  requirementMode === "manual" ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted"
                }`}
              >
                Manual override
              </button>
            </div>

            <div className="space-y-1">
              <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {requirementMode === "auto" ? "Kairo needed" : "Manual Kairo needed"}
              </label>
              <ThemedNumberInput
                value={targetCount}
                min={0}
                onValueChange={(value) => setTargetCount(Math.max(0, value))}
                disabled={requirementMode === "auto"}
              />
            </div>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardContent className="p-3">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Total copper coins</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{route.totalBuy}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-3">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Total items to trade</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{route.totalExchange}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-3">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Full route total</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{route.totalCombined}</div>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Best route summary</h2>
            <div className="text-xs text-muted-foreground">
              {route.usedEntries.length} source item{route.usedEntries.length === 1 ? "" : "s"} used
            </div>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {route.usedEntries.map((state) => {
              const change = unavailableSourceIds.size > 0 ? routeChangesByInputId[state.entry.inputId] : undefined;
              return (
              <div
                key={state.entry.inputId}
                className={`rounded-md border px-3 py-2 text-xs ${
                  change ? "border-amber-400/70 bg-amber-500/10" : "border-border bg-background/60"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2 font-medium">
                    {getEquipmentIcon(equipIcons, state.entry.inputName) && (
                      <img src={getEquipmentIcon(equipIcons, state.entry.inputName)} alt="" className="h-5 w-5 shrink-0 rounded object-contain" />
                    )}
                    <span className="truncate">{state.entry.inputName}</span>
                  </span>
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    <span className={`tabular-nums font-semibold ${change ? "text-amber-700 dark:text-amber-300" : ""}`}>
                      Buy {change ? `${change.previousExchange} -> ${state.totalExchange}` : state.totalExchange}
                    </span>
                    <button
                      type="button"
                      title="Unavailable or not unlocked - remove from this route"
                      onClick={() => {
                        setUnavailableSourceIds((prev) => {
                          const next = new Set(prev);
                          next.add(state.entry.inputId);
                          return next;
                        });
                      }}
                      className="flex h-6 items-center gap-1 rounded-md border border-amber-500/50 bg-amber-500/10 px-2 text-[10px] font-medium text-amber-700 hover:bg-amber-500/20 dark:text-amber-300"
                    >
                      <Ban className="h-3.5 w-3.5" />
                      Unavailable
                    </button>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-muted-foreground">marks done &amp; updates rate ➜</span>
                      <button
                        type="button"
                        title="Mark as bought & traded — updates your trade rate and reduces target"
                        onClick={() => {
                          setCurrentPriceInputs((prev) => ({ ...prev, [state.entry.inputId]: String(state.nextExchange) }));
                          if (requirementMode === "auto") {
                            setOwnedKairo((prev) => prev + state.used);
                          } else {
                            setTargetCount((prev) => Math.max(0, prev - state.used));
                          }
                        }}
                        className="flex h-6 w-6 items-center justify-center rounded-md border border-primary/50 bg-primary/10 text-primary hover:bg-primary/20"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">{state.totalExchange} × {state.entry.buyPrice}¢ = {state.totalBuy}¢ total &nbsp;·&nbsp; used {state.used}×</div>
                {change && (
                  <div className="mt-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                    Changed from Buy {change.previousExchange} / {change.previousBuy}c before unavailable items were removed.
                  </div>
                )}
                <div className="mt-0.5 text-[11px] text-muted-foreground">Rate after: <span className="font-medium text-foreground">{state.nextExchange}</span></div>
              </div>
              );
            })}
          </div>
          {unavailableSourceIds.size > 0 && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              {unavailableSourceIds.size} unavailable source item{unavailableSourceIds.size === 1 ? "" : "s"} removed from the route.
              Yellow rows changed compared with the route before unavailable items were removed.
            </div>
          )}
        </div>

        <details className="rounded-md border border-border bg-background/40">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-foreground">Show per-trade route</summary>
          <div className="border-t border-border px-3 py-2">
            <p className="mb-2 text-[11px] text-muted-foreground">Each line is one exchange. You buy that many copies of the item, then trade them all for 1 Kairo weapon.</p>
            <div className="grid gap-1">
              {route.picks.map((pick, index) => (
                <div key={`${pick.item}-${index}`} className="flex items-center justify-between gap-3 text-xs">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="shrink-0">{index + 1}.</span>
                    {getEquipmentIcon(equipIcons, pick.item) && (
                      <img src={getEquipmentIcon(equipIcons, pick.item)} alt="" className="h-4 w-4 shrink-0 rounded object-contain" />
                    )}
                    <span className="truncate">{pick.item}</span>
                  </span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {pick.tradeCost} × {pick.buyCost}¢ = <span className="font-medium text-foreground">{pick.totalCost}¢</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        </details>

        <div className="space-y-2">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Source items</h2>
            <div className="flex gap-2 sm:items-center">
              <Input
                value={sourceQuery}
                onChange={(e) => setSourceQuery(e.target.value)}
                placeholder="Filter source items"
                className="h-8 text-xs sm:w-48"
              />
              <button
                type="button"
                onClick={resetVisible}
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
              >
                Reset current trades
              </button>
              {unavailableSourceIds.size > 0 && (
                <button
                  type="button"
                  onClick={() => setUnavailableSourceIds(new Set())}
                  className="rounded-md border border-amber-500/50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-500/10 dark:text-amber-300"
                >
                  Clear unavailable
                </button>
              )}
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="flex flex-wrap gap-3">
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Rank:</span>
                {RANK_TOGGLES.map((rank) => (
                <button
                  key={rank}
                  type="button"
                  onClick={() => setSourceRankFilters((prev) => { const next = new Set(prev); next.has(rank) ? next.delete(rank) : next.add(rank); return next; })}
                  className={`rounded-md border px-2 py-0.5 text-xs font-medium transition-colors ${
                    sourceRankFilters.has(rank) ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted"
                  }`}
                >
                  {rank}
                </button>
              ))}
              {sourceRankFilters.size > 0 && (
                <button type="button" onClick={() => setSourceRankFilters(new Set())} className="text-[10px] text-muted-foreground hover:text-foreground underline">clear</button>
              )}
            </div>
            <div className="relative flex items-center gap-1" ref={givesDropdownRef}>
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Gives:</span>
              <button
                type="button"
                onClick={() => setGivesDropdownOpen((o) => !o)}
                className="flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-xs font-medium hover:bg-muted"
              >
                {sourceGivesFilters.size === 0
                  ? "All"
                  : sourceGivesFilters.size === 1
                  ? [...sourceGivesFilters][0].replace("A/ ", "")
                  : `${[...sourceGivesFilters][0].replace("A/ ", "")} +${sourceGivesFilters.size - 1}`}
                <svg className="h-3 w-3 opacity-60" viewBox="0 0 12 12" fill="none"><path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </button>
              {sourceGivesFilters.size > 0 && (
                <button type="button" onClick={() => setSourceGivesFilters(new Set())} className="text-[10px] text-muted-foreground hover:text-foreground underline">clear</button>
              )}
              {givesDropdownOpen && (
                <div
                  className="absolute left-0 top-full z-50 mt-1 min-w-[160px] rounded-md border border-border bg-popover shadow-md"
                  onMouseDown={(e) => e.preventDefault()}
                >
                  {KAIRO_OUTPUTS.map((output) => (
                    <label
                      key={output}
                      className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs hover:bg-muted"
                    >
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 accent-primary"
                        checked={sourceGivesFilters.has(output)}
                        onChange={() => setSourceGivesFilters((prev) => { const next = new Set(prev); next.has(output) ? next.delete(output) : next.add(output); return next; })}
                      />
                      {output.replace("A/ ", "")}
                    </label>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background/50 p-3">
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={copyExportText}>
                <Upload className="mr-2 h-3.5 w-3.5" />Export state
              </Button>
              <Button size="sm" variant="outline" onClick={pasteImportText}>
                <Download className="mr-2 h-3.5 w-3.5" />Import state
              </Button>
            </div>
            {clipboardStatus && (
              <div
                className={`flex items-center gap-1.5 text-xs ${
                  clipboardStatus.type === "ok" ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                }`}
              >
                {clipboardStatus.type === "ok" ? <Check className="h-3.5 w-3.5" /> : null}
                {clipboardStatus.message}
              </div>
            )}
          </div>

          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[480px] sm:min-w-[840px] border-collapse text-xs">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-right font-medium text-muted-foreground">#</th>
                  <th className="px-3 py-2 text-left font-medium">Item</th>
                  <th className="px-3 py-2 text-left font-medium">Rank</th>
                  <th className="px-3 py-2 text-left font-medium">Gives</th>
                  <th className="px-3 py-2 text-right font-medium">Buy price</th>
                  <th className="px-3 py-2 text-right font-medium">Start trade price</th>
                  <th className="hidden px-3 py-2 text-right font-medium sm:table-cell">Step</th>
                  <th className="px-3 py-2 text-right font-medium">Your current trade</th>
                  <th className="px-3 py-2 text-center font-medium">Availability</th>
                  <th className="hidden px-3 py-2 text-right font-medium sm:table-cell">Total copper cost</th>
                </tr>
              </thead>
              <tbody>
                {filteredEntries.map((entry, index) => {
                  const raw = currentPriceInputs[entry.inputId] ?? String(entry.startPrice);
                  const parsed = Number(raw);
                  const isValid = raw.trim() === "" || isValidTradeValue(parsed, entry.startPrice, entry.priceStep);
                  const current = isValid && raw.trim() !== "" ? parsed : entry.startPrice;
                  const isUnavailable = unavailableSourceIds.has(entry.inputId);
                  return (
                    <tr key={entry.inputId} className={`border-t border-border ${isUnavailable ? "bg-amber-500/10 text-muted-foreground" : ""}`}>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{index + 1}</td>
                      <td className="px-3 py-2 font-medium">
                        <div className="flex items-center gap-2">
                          {getEquipmentIcon(equipIcons, entry.inputName) ? (
                            <img src={getEquipmentIcon(equipIcons, entry.inputName)} alt="" className="h-5 w-5 rounded object-contain" />
                          ) : (
                            <span className="h-5 w-5" />
                          )}
                          <span>{entry.inputName}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">{entry.rankLabel}</td>
                      <td className="px-3 py-2 text-muted-foreground">{entry.outputName.replace("A/ ", "")}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{entry.buyPrice}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{entry.startPrice}</td>
                      <td className="hidden px-3 py-2 text-right tabular-nums sm:table-cell">+{entry.priceStep}</td>
                      <td className="px-3 py-2">
                        <ThemedNumberInput
                          value={raw}
                          min={entry.startPrice}
                          step={entry.priceStep}
                          onRawChange={(value) =>
                            setCurrentPriceInputs((prev) => ({
                              ...prev,
                              [entry.inputId]: value,
                            }))
                          }
                          className={`ml-auto w-28 ${isValid ? "" : "border-red-500"}`}
                        />
                        {!isValid && (
                          <div className="mt-1 text-[10px] text-red-500">
                            Must follow {entry.startPrice} + n x {entry.priceStep}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <button
                          type="button"
                          onClick={() =>
                            setUnavailableSourceIds((prev) => {
                              const next = new Set(prev);
                              if (next.has(entry.inputId)) next.delete(entry.inputId);
                              else next.add(entry.inputId);
                              return next;
                            })
                          }
                          className={`inline-flex h-7 items-center gap-1 rounded-md border px-2 text-[10px] font-medium ${
                            isUnavailable
                              ? "border-amber-500/60 bg-amber-500/15 text-amber-700 dark:text-amber-300"
                              : "border-border hover:bg-muted"
                          }`}
                        >
                          <Ban className="h-3.5 w-3.5" />
                          {isUnavailable ? "Unavailable" : "Available"}
                        </button>
                      </td>
                      <td className="hidden px-3 py-2 text-right tabular-nums sm:table-cell">{current * entry.buyPrice}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-3 rounded-md border border-border bg-background/50 p-3">
          <div className="space-y-1">
            <h2 className="text-sm font-semibold">Fun facts</h2>
            <p className="text-xs text-muted-foreground">
              Total Kairo gear and diamonds needed to unlock smithy breaks across bigger equipment collections.
            </p>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <Card>
              <CardContent className="p-3">
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground">All game equipment</div>
                <div className="mt-1 text-sm font-semibold">{allGameFacts.totalKairo} Kairo</div>
                <div className="text-xs text-muted-foreground">
                  {allGameFacts.diamond} diamonds across {allGameFacts.equipmentCount} items
                </div>
                <div className="text-xs text-muted-foreground">Cheapest buy route: {allGameCopper} copper coins</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-3">
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Every S-rank item</div>
                <div className="mt-1 text-sm font-semibold">{allSFacts.totalKairo} Kairo</div>
                <div className="text-xs text-muted-foreground">
                  {allSFacts.diamond} diamonds across {allSFacts.equipmentCount} items
                </div>
                <div className="text-xs text-muted-foreground">Cheapest buy route: {allSCopper} copper coins</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-3">
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Every A-rank item</div>
                <div className="mt-1 text-sm font-semibold">{allAFacts.totalKairo} Kairo</div>
                <div className="text-xs text-muted-foreground">
                  {allAFacts.diamond} diamonds across {allAFacts.equipmentCount} items
                </div>
                <div className="text-xs text-muted-foreground">Cheapest buy route: {allACopper} copper coins</div>
              </CardContent>
            </Card>
          </div>

          <div className="space-y-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Custom combination by rank</div>
            <div className="flex flex-wrap gap-2">
              {RANK_TOGGLES.map((rank) => (
                <button
                  key={rank}
                  type="button"
                  onClick={() => toggleFunFactRank(rank)}
                  className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
                    funFactRanks.has(rank) ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted"
                  }`}
                >
                  {rank}
                </button>
              ))}
            </div>

            <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
              <Card>
                <CardContent className="p-3">
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Custom total</div>
                  <div className="mt-1 text-lg font-semibold tabular-nums">{funFacts.totalKairo} Kairo</div>
                  <div className="text-xs text-muted-foreground">
                    {funFacts.diamond} diamonds across {funFacts.equipmentCount} items
                  </div>
                  <div className="text-xs text-muted-foreground">Cheapest buy route: {funFactsCopper} copper coins</div>
                </CardContent>
              </Card>

              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                {KAIRO_OUTPUTS.map((output) => (
                  <div key={output} className="rounded-md border border-border bg-background/60 px-3 py-2 text-xs">
                    <div className="font-medium">{output.replace("A/ ", "")}</div>
                    <div className="mt-1 tabular-nums text-muted-foreground">{funFacts.counts[output]}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
