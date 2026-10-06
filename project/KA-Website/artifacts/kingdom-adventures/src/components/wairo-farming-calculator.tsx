import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Calculator, Check, Info, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ThemedNumberInput } from "@/components/ui/themed-number-input";
import { matchesLooseSearch } from "@/lib/search-normalize";
import { WAIRO_DUNGEON_LOOT_GROUP } from "@/lib/special-boss-loot";
import {
  wairoRunEnergy,
  encounterExpectedDrops,
  getAllWairoItems,
  WAIRO_DIFFICULTIES,
  WAIRO_ENERGY_STOREHOUSE_CAPACITY,
  WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY,
  type WairoCollectSource,
  type WairoReplenishPlan,
  type Difficulty,
} from "@/lib/farming-calc";
import { useEquipmentIcons } from "@/hooks/use-equipment-icons";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { getTownRankStaminaCapacity, getTownRankStaminaPumpCapacity } from "@/pages/town-rank";
import type { WairoRouteWorkerInput, WairoRouteWorkerResponse } from "@/workers/wairo-route-planner.worker";

const ALL_ITEMS = getAllWairoItems();

type ResultRow = {
  difficulty: Difficulty;
  energyPerRun: number;
  expectedPerRun: number;
  energyPerItem: number;
  runsForN: number;
  totalEnergy: number;
};

type PlannerInputs = WairoRouteWorkerInput;

const WAIRO_ROUTE_SETUP_STORAGE_KEY = "ka_wairo_route_setup";
const STAMINA_COLLECT_BUTTON_ICON = "/website_icons/requested/stamina_collect_button.png";
const DESTRUCT_BUTTON_ICON = "/website_icons/requested/destruct.png";
const MENU_BUILD_ICON = "/website_icons/menu/menu_build.png";
const CONQUEST_MEMBER_ICON = "/website_icons/requested/conquest_member_icon.png";

type StoredRouteSetup = {
  routeMines?: number;
  townhallCount?: number;
  townhallLevels?: number[];
  energyStorehouses?: number;
  hgEnergyStorehouses?: number;
  targetDifficulty?: Difficulty;
};

function readStoredRouteSetup(): StoredRouteSetup {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(WAIRO_ROUTE_SETUP_STORAGE_KEY) || "{}") as StoredRouteSetup;
  } catch {
    return {};
  }
}

export function WairoFarmingCalculator() {
  const storedRouteSetup = useMemo(() => readStoredRouteSetup(), []);
  const equipIcons = useEquipmentIcons();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [maxEnergy, setMaxEnergy] = useState(500);
  const [mines, setMines] = useState(0);
  const [desired, setDesired] = useState(1);
  const [routeCurrentEnergy, setRouteCurrentEnergy] = useState(0);
  const [routeCurrentEnergyEdited, setRouteCurrentEnergyEdited] = useState(false);
  const [routeMines, setRouteMines] = useState(storedRouteSetup.routeMines ?? 0);
  const [refillItems, setRefillItems] = useState(1);
  const [mineHeldEnergy, setMineHeldEnergy] = useState<number[]>([]);
  const [townhallCount, setTownhallCount] = useState(storedRouteSetup.townhallCount ?? 0);
  const [townhallLevels, setTownhallLevels] = useState<number[]>(storedRouteSetup.townhallLevels ?? []);
  const [townhallHeldEnergy, setTownhallHeldEnergy] = useState<number[]>([]);
  const [energyStorehouses, setEnergyStorehouses] = useState(storedRouteSetup.energyStorehouses ?? 0);
  const [hgEnergyStorehouses, setHgEnergyStorehouses] = useState(storedRouteSetup.hgEnergyStorehouses ?? 0);
  const [targetDifficulty, setTargetDifficulty] = useState<Difficulty>(storedRouteSetup.targetDifficulty ?? "Extreme");
  const [replenishPlan, setReplenishPlan] = useState<WairoReplenishPlan | null>(null);
  const [isCalculatingRoute, setIsCalculatingRoute] = useState(false);
  const [routeCalculationSeconds, setRouteCalculationSeconds] = useState(0);
  const [routeError, setRouteError] = useState<string | null>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const workerRef = useRef<Worker | null>(null);
  const routeRequestIdRef = useRef(0);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    const worker = new Worker(new URL("../workers/wairo-route-planner.worker.ts", import.meta.url), {
      type: "module",
    });
    workerRef.current = worker;
    worker.onmessage = (event: MessageEvent<WairoRouteWorkerResponse>) => {
      if (event.data.id !== routeRequestIdRef.current) return;
      setIsCalculatingRoute(false);
      if (event.data.ok) {
        setReplenishPlan(event.data.plan);
        setRouteError(null);
      } else {
        setRouteError(event.data.error);
      }
    };
    worker.onerror = () => {
      setIsCalculatingRoute(false);
      setRouteError("Route calculation failed in the worker.");
    };
    return () => {
      worker.terminate();
      workerRef.current = null;
    };
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      WAIRO_ROUTE_SETUP_STORAGE_KEY,
      JSON.stringify({
        routeMines,
        townhallCount,
        townhallLevels,
        energyStorehouses,
        hgEnergyStorehouses,
        targetDifficulty,
      } satisfies StoredRouteSetup),
    );
  }, [energyStorehouses, hgEnergyStorehouses, routeMines, targetDifficulty, townhallCount, townhallLevels]);

  useEffect(() => {
    if (!isCalculatingRoute) return;
    const interval = window.setInterval(() => {
      setRouteCalculationSeconds((seconds) => seconds + 1);
    }, 1000);
    return () => window.clearInterval(interval);
  }, [isCalculatingRoute]);

  const filteredItems = useMemo(
    () => (query.trim() ? ALL_ITEMS.filter((i) => matchesLooseSearch(i, query)) : ALL_ITEMS),
    [query],
  );
  const selectedIcon = getEquipmentIcon(equipIcons, selectedItem);
  const recoverEnergyIcon = getEquipmentIcon(equipIcons, "Recover Energy") ?? "/website_icons/items/item_026.png";

  function selectItem(item: string) {
    setSelectedItem(item);
    setQuery(item);
    setOpen(false);
  }

  const costFactor = Math.max(50 * mines, maxEnergy);
  const minesDominating = 50 * mines > maxEnergy;

  const results = useMemo((): ResultRow[] => {
    if (!selectedItem) return [];
    return WAIRO_DUNGEON_LOOT_GROUP.encounters
      .flatMap((enc) => {
        const energyPerRun = wairoRunEnergy(enc.difficulty, mines, maxEnergy);
        const expectedPerRun = encounterExpectedDrops(enc, selectedItem);
        if (expectedPerRun <= 0) return [];
        const energyPerItem = energyPerRun / expectedPerRun;
        const runsForN = Math.ceil(desired / expectedPerRun);
        return [
          {
            difficulty: enc.difficulty,
            energyPerRun,
            expectedPerRun,
            energyPerItem,
            runsForN,
            totalEnergy: runsForN * energyPerRun,
          } satisfies ResultRow,
        ];
      })
      .sort((a, b) => a.energyPerItem - b.energyPerItem);
  }, [selectedItem, maxEnergy, mines, desired]);

  const best = results[0] ?? null;
  const collectSources = useMemo(
    (): WairoCollectSource[] => [
      ...Array.from({ length: Math.max(0, Math.floor(routeMines)) }, (_, index) => ({
        label: `Mine ${index + 1}`,
        amount: mineHeldEnergy[index] ?? 50,
      })).filter((source) => source.amount > 0),
      ...Array.from({ length: Math.min(5, Math.max(0, Math.floor(townhallCount))) }, (_, index) => ({
        label: `Townhall ${index + 1}`,
        amount: townhallHeldEnergy[index] ?? getTownRankStaminaPumpCapacity(townhallLevels[index] ?? 0),
      })).filter((source) => source.amount > 0),
    ],
    [mineHeldEnergy, routeMines, townhallCount, townhallHeldEnergy, townhallLevels],
  );
  const routeMaxEnergy =
    energyStorehouses * WAIRO_ENERGY_STOREHOUSE_CAPACITY +
    hgEnergyStorehouses * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY +
    Array.from({ length: Math.min(5, Math.max(0, Math.floor(townhallCount))) }, (_, index) =>
      getTownRankStaminaCapacity(townhallLevels[index] ?? 0),
    ).reduce((sum, capacity) => sum + capacity, 0);

  useEffect(() => {
    if (!routeCurrentEnergyEdited) {
      setRouteCurrentEnergy(routeMaxEnergy);
    }
  }, [routeCurrentEnergyEdited, routeMaxEnergy]);

  const [plannerInputs, setPlannerInputs] = useState<PlannerInputs>({
    currentEnergy: routeCurrentEnergy,
    maxEnergy: routeMaxEnergy,
    mines: routeMines,
    energyStorehouses,
    hgEnergyStorehouses,
    targetDifficulty,
    refillItems,
    collectSources,
  });
  const plannerHasPendingChanges =
    plannerInputs.currentEnergy !== routeCurrentEnergy ||
    plannerInputs.maxEnergy !== routeMaxEnergy ||
    plannerInputs.mines !== routeMines ||
    plannerInputs.energyStorehouses !== energyStorehouses ||
    plannerInputs.hgEnergyStorehouses !== hgEnergyStorehouses ||
    plannerInputs.targetDifficulty !== targetDifficulty ||
    plannerInputs.refillItems !== refillItems ||
    JSON.stringify(plannerInputs.collectSources) !== JSON.stringify(collectSources);

  function calculateRoute() {
    const nextInputs = {
      currentEnergy: routeCurrentEnergy,
      maxEnergy: routeMaxEnergy,
      mines: routeMines,
      energyStorehouses,
      hgEnergyStorehouses,
      targetDifficulty,
      refillItems,
      collectSources,
    } satisfies PlannerInputs;
    setPlannerInputs(nextInputs);
    const worker = workerRef.current;
    if (!worker) {
      setRouteError("Route calculator is still starting. Try Calculate again in a moment.");
      return;
    }
    const id = routeRequestIdRef.current + 1;
    routeRequestIdRef.current = id;
    setIsCalculatingRoute(true);
    setRouteCalculationSeconds(0);
    setRouteError(null);
    worker.postMessage({ id, input: nextInputs });
  }

  function updateRouteCurrentEnergy(value: number) {
    setRouteCurrentEnergyEdited(true);
    setRouteCurrentEnergy(value);
  }
  const totalDeletableCapacity =
    energyStorehouses * WAIRO_ENERGY_STOREHOUSE_CAPACITY +
    hgEnergyStorehouses * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
  const mineThreshold = Math.floor(maxEnergy / 50) + 1;

  function fmtEnergy(n: number) {
    return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1);
  }

  function fmtStorageChange(normal: number, hg: number) {
    const parts = [];
    if (normal > 0) parts.push(`${normal} Energy`);
    if (hg > 0) parts.push(`${hg} HG Energy`);
    return parts.length > 0 ? parts.join(", ") : "none";
  }

  function fmtCollectActions(actions: { sourceLabel: string; amount: number; remainingInSource: number }[]) {
    return actions
      .map((action) => `${fmtEnergy(action.amount)} from ${action.sourceLabel} (${fmtEnergy(action.remainingInSource)} left)`)
      .join(", ");
  }

  function fmtRunSummary(targetRuns: number, totalRuns: number) {
    const fallbackRuns = totalRuns - targetRuns;
    return `${targetRuns.toLocaleString()} ${plannerInputs.targetDifficulty} run${targetRuns === 1 ? "" : "s"}${
      fallbackRuns > 0 ? ` + ${fallbackRuns.toLocaleString()} fallback run${fallbackRuns === 1 ? "" : "s"}` : ""
    }`;
  }

  function fmtDuration(seconds: number) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
  }

  function updateMineHeldEnergy(index: number, value: number) {
    setMineHeldEnergy((current) => {
      const next = [...current];
      next[index] = Math.max(0, value);
      return next;
    });
  }

  function updateTownhallHeldEnergy(index: number, value: number) {
    setTownhallHeldEnergy((current) => {
      const next = [...current];
      next[index] = Math.max(0, value);
      return next;
    });
  }

  function updateTownhallLevel(index: number, value: number) {
    const level = Math.max(0, Math.floor(value));
    setTownhallLevels((current) => {
      const next = [...current];
      next[index] = level;
      return next;
    });
    setTownhallHeldEnergy((current) => {
      const next = [...current];
      next[index] = getTownRankStaminaPumpCapacity(level);
      return next;
    });
  }

  function ActionIcon({
    children,
    className = "",
  }: {
    children: ReactNode;
    className?: string;
  }) {
    return (
      <span className={`inline-flex min-h-[34px] shrink-0 items-stretch justify-center self-stretch overflow-hidden leading-none ${className}`}>
        {children}
      </span>
    );
  }

  return (
    <div className="space-y-4">
    <Card id="wairo-farming-calc" className="shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Calculator className="w-4 h-4 text-primary" />
          Farming Calculator
        </CardTitle>
        <CardDescription>
          Expected energy cost to farm a specific drop. Wairo energy cost scales with your max energy and mine count.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Inputs */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {/* Item combobox */}
          <div className="space-y-1 sm:col-span-2 lg:col-span-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Target item
            </label>
            <div className="relative" ref={dropRef}>
              <Input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setOpen(true);
                }}
                onFocus={() => setOpen(true)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setOpen(false);
                  if (e.key === "Enter" && filteredItems.length > 0) selectItem(filteredItems[0]);
                }}
                placeholder="Search item…"
                className={`h-9 pr-9 ${selectedIcon ? "pl-9" : ""} ${open ? "rounded-b-none border-primary ring-1 ring-primary" : ""}`}
                role="combobox"
                aria-expanded={open}
                aria-controls="wairo-item-results"
              />
              {selectedIcon && (
                <img src={selectedIcon} alt="" className="pointer-events-none absolute left-2 top-1/2 h-5 w-5 -translate-y-1/2 rounded object-contain" />
              )}
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  setOpen((o) => !o);
                }}
                className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded text-primary hover:bg-primary/10"
                aria-label={open ? "Close item list" : "Open item list"}
              >
                <svg
                  className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
                  viewBox="0 0 12 12"
                  fill="none"
                >
                  <path
                    d="M2 4l4 4 4-4"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
              {open && (
                <div
                  id="wairo-item-results"
                  className="absolute left-0 right-0 top-full z-50 max-h-64 overflow-y-auto rounded-b-md border border-t-0 border-primary bg-popover shadow-lg"
                >
                  {filteredItems.length === 0 ? (
                    <div className="px-3 py-3 text-center text-xs text-muted-foreground">
                      No matching items
                    </div>
                  ) : (
                    filteredItems.map((item) => {
                      const icon = getEquipmentIcon(equipIcons, item);
                      return (
                        <button
                          key={item}
                          type="button"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            selectItem(item);
                          }}
                          className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted ${
                            item === selectedItem ? "bg-primary/10 font-medium text-foreground" : ""
                          }`}
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            {icon ? <img src={icon} alt="" className="h-5 w-5 shrink-0 rounded object-contain" /> : <span className="h-5 w-5 shrink-0" />}
                            <span className="truncate">{item}</span>
                          </span>
                          {item === selectedItem && (
                            <Check className="h-3.5 w-3.5 shrink-0 text-primary" />
                          )}
                        </button>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Max energy
            </label>
            <ThemedNumberInput value={maxEnergy} min={1} onValueChange={setMaxEnergy} />
            <div className="text-[10px] text-muted-foreground">Storage + town hall total</div>
          </div>

          <div className="space-y-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Energy mines
            </label>
            <ThemedNumberInput value={mines} min={0} max={50} onValueChange={setMines} />
            <div className="text-[10px] text-muted-foreground">0 – 50</div>
          </div>

          <div className="space-y-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Items wanted
            </label>
            <ThemedNumberInput value={desired} min={1} onValueChange={setDesired} />
          </div>
        </div>

        {/* Cost factor info */}
        <div className="rounded-md border bg-muted/20 px-3 py-2 space-y-2 text-xs text-muted-foreground">
          <div>
            <span className="font-medium text-foreground">Energy / run formula: </span>
            <span className="font-mono">base × max(50 × mines, max_energy) / 100</span>
          </div>
          <div className="grid gap-1">
            <div>
              Step 1 — inner max:{" "}
              <span className="font-mono">
                max(50 × {mines}, {maxEnergy.toLocaleString()}) = {costFactor.toLocaleString()}
              </span>
              {minesDominating ? (
                <span className="ml-2 text-amber-600 dark:text-amber-400">mines are dominating</span>
              ) : (
                <span className="ml-2">(max energy cap wins)</span>
              )}
            </div>
            <div>
              Step 2 — multiplier:{" "}
              <span className="font-mono">
                {costFactor.toLocaleString()} / 100 = {(costFactor / 100).toFixed(2)}
              </span>
            </div>
            {!minesDominating && (
              <div>
                Mine count will not change Wairo cost at this cap until Energy mines reaches{" "}
                <span className="font-mono">{mineThreshold.toLocaleString()}</span>.
              </div>
            )}
            <div className="pt-1 grid gap-0.5">
              {(["Easy", "Normal", "Hard", "Extreme"] as Difficulty[]).map((d) => {
                const bases: Record<Difficulty, number> = { Easy: 60, Normal: 65, Hard: 70, Extreme: 80 };
                const base = bases[d];
                const cost = Math.max(base * costFactor / 100, 1);
                return (
                  <div key={d}>
                    <span className="font-medium text-foreground">{d}:</span>{" "}
                    <span className="font-mono">
                      {base} × {(costFactor / 100).toFixed(2)} = {cost % 1 === 0 ? cost.toLocaleString() : cost.toFixed(1)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Results */}
        {!selectedItem ? (
          <div className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
            Select a target item above to see farming efficiency across Wairo Dungeon difficulties.
          </div>
        ) : results.length === 0 ? (
          <div className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
            {selectedItem} does not drop in any Wairo Dungeon difficulty.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-xs">
              <thead className="bg-muted/30 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Difficulty</th>
                  <th className="px-3 py-2 text-right font-medium">Energy / run</th>
                  <th className="px-3 py-2 text-right font-medium">Avg / run</th>
                  <th className="px-3 py-2 text-right font-medium">Energy / item</th>
                  <th className="px-3 py-2 text-right font-medium">Runs for {desired}</th>
                  <th className="px-3 py-2 text-right font-medium">Total energy</th>
                </tr>
              </thead>
              <tbody>
                {results.map((row) => {
                  const isBest = row === best;
                  return (
                    <tr
                      key={row.difficulty}
                      className={`border-t border-border/60 ${isBest ? "bg-green-500/10" : ""}`}
                    >
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Badge variant="secondary" className="text-[11px]">
                            {row.difficulty}
                          </Badge>
                          {isBest && (
                            <Badge
                              variant="outline"
                              className="text-[10px] border-green-500/50 text-green-600 dark:text-green-400"
                            >
                              Best
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {fmtEnergy(row.energyPerRun)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {row.expectedPerRun >= 0.001
                          ? row.expectedPerRun.toFixed(4)
                          : row.expectedPerRun.toExponential(2)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right tabular-nums font-semibold ${
                          isBest ? "text-green-600 dark:text-green-400" : "text-foreground"
                        }`}
                      >
                        {Math.round(row.energyPerItem).toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {row.runsForN.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {Math.round(row.totalEnergy).toLocaleString()}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {results.length > 0 && (
          <p className="text-[11px] text-muted-foreground">
            Energy / run = base cost × max(50 × mines, max energy) ÷ 100. Each run picks one of two loot tables at random (50/50 assumed).
          </p>
        )}
      </CardContent>
    </Card>
    <Card id="wairo-replenish-route-planner" className="shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Calculator className="w-4 h-4 text-primary" />
          Replenish Route Planner
        </CardTitle>
        <CardDescription>
          Plan Wairo runs around deleteable energy storage, then rebuild the smallest cap that keeps the same run count.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-5">
          <section className="space-y-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-foreground">Town setup</div>
              <div className="text-[11px] text-muted-foreground">These are the parts of your town that usually stay the same.</div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Energy Storehouse
                </label>
                <ThemedNumberInput value={energyStorehouses} min={0} onValueChange={setEnergyStorehouses} />
                <div className="text-[10px] text-muted-foreground">
                  {WAIRO_ENERGY_STOREHOUSE_CAPACITY} energy each
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  HG Energy Storehouse
                </label>
                <ThemedNumberInput value={hgEnergyStorehouses} min={0} onValueChange={setHgEnergyStorehouses} />
                <div className="text-[10px] text-muted-foreground">
                  {WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY} energy each
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Energy mines
                </label>
                <ThemedNumberInput
                  value={routeMines}
                  min={0}
                  max={50}
                  onValueChange={(value) => setRouteMines(Math.max(0, Math.floor(value)))}
                />
                <div className="text-[10px] text-muted-foreground">Adds mine held-energy boxes below</div>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Townhalls
                </label>
                <ThemedNumberInput
                  value={townhallCount}
                  min={0}
                  max={5}
                  onValueChange={(value) => setTownhallCount(Math.min(5, Math.max(0, Math.floor(value))))}
                />
                <div className="text-[10px] text-muted-foreground">Adds townhall level and held-energy boxes</div>
              </div>
            </div>
          </section>

          {(routeMines > 0 || townhallCount > 0) && (
            <section className="space-y-3 border-t pt-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-foreground">Collectable energy sources</div>
                <div className="text-[11px] text-muted-foreground">Mines and townhalls both feed extra energy into storage, so they live together here.</div>
              </div>
              <div className="space-y-4">
                {routeMines > 0 && (
                  <div className="space-y-2">
                    <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Mines</div>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                      {Array.from({ length: Math.max(0, Math.floor(routeMines)) }, (_, index) => (
                        <div key={`mine-${index}`} className="space-y-1">
                          <label className="text-[11px] text-muted-foreground">Mine {index + 1}</label>
                          <ThemedNumberInput
                            value={mineHeldEnergy[index] ?? 50}
                            min={0}
                            max={50}
                            onValueChange={(value) => updateMineHeldEnergy(index, value)}
                          />
                          <div className="text-[10px] text-muted-foreground">Full: 50</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {townhallCount > 0 && (
                  <div className="space-y-2">
                    <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Townhalls</div>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                      {Array.from({ length: Math.min(5, Math.max(0, Math.floor(townhallCount))) }, (_, index) => (
                        <div key={`townhall-${index}`} className="space-y-2 rounded-md border bg-background/40 p-2">
                          <div className="text-[11px] font-medium text-muted-foreground">Townhall {index + 1}</div>
                          <div className="grid grid-cols-2 gap-2">
                            <div className="space-y-1">
                              <label className="text-[11px] text-muted-foreground">Level</label>
                              <ThemedNumberInput
                                value={townhallLevels[index] ?? 0}
                                min={0}
                                onValueChange={(value) => updateTownhallLevel(index, value)}
                              />
                            </div>
                            <div className="space-y-1">
                              <label className="text-[11px] text-muted-foreground">Held</label>
                              <ThemedNumberInput
                                value={townhallHeldEnergy[index] ?? getTownRankStaminaPumpCapacity(townhallLevels[index] ?? 0)}
                                min={0}
                                max={getTownRankStaminaPumpCapacity(townhallLevels[index] ?? 0)}
                                onValueChange={(value) => updateTownhallHeldEnergy(index, value)}
                              />
                            </div>
                          </div>
                          <div className="text-[10px] text-muted-foreground">
                            Full pump: {getTownRankStaminaPumpCapacity(townhallLevels[index] ?? 0).toLocaleString()}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </section>
          )}

          <section className="space-y-3 border-t pt-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-foreground">Run setup</div>
              <div className="text-[11px] text-muted-foreground">Set the event choices and your current energy after the town setup is known.</div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Target difficulty
                </label>
                <select
                  value={targetDifficulty}
                  onChange={(e) => setTargetDifficulty(e.target.value as Difficulty)}
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  {WAIRO_DIFFICULTIES.map((difficulty) => (
                    <option key={difficulty} value={difficulty}>
                      {difficulty}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Refill items
                </label>
                <ThemedNumberInput value={refillItems} min={0} onValueChange={setRefillItems} />
                <div className="text-[10px] text-muted-foreground">Items to spend after starting energy</div>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Current energy
                </label>
                <ThemedNumberInput value={routeCurrentEnergy} min={0} max={routeMaxEnergy} onValueChange={updateRouteCurrentEnergy} />
                <div className="text-[10px] text-muted-foreground">
                  {routeCurrentEnergyEdited ? "Manual energy before the first run" : "Auto-filled from calculated max"}
                </div>
              </div>

              <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs">
                <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Calculated max energy
                </div>
                <div className="mt-1 text-lg font-semibold tabular-nums text-foreground">
                  {routeMaxEnergy.toLocaleString()}
                </div>
                <div className="text-[10px] text-muted-foreground">Townhall cap + storage units</div>
              </div>
            </div>
          </section>

          <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="text-[11px]">
                  Deleteable cap: {totalDeletableCapacity.toLocaleString()}
                </Badge>
                {replenishPlan ? (
                  <>
                    <Badge variant="outline" className="text-[11px]">
                      {replenishPlan.targetRuns.toLocaleString()} {plannerInputs.targetDifficulty} run{replenishPlan.targetRuns === 1 ? "" : "s"}
                    </Badge>
                    <Badge variant="outline" className="text-[11px]">
                      {replenishPlan.cycles.length.toLocaleString()} cycle{replenishPlan.cycles.length === 1 ? "" : "s"}
                    </Badge>
                    <Badge variant="outline" className="text-[11px]">
                      {(replenishPlan.totalRuns - replenishPlan.targetRuns).toLocaleString()} fallback run{replenishPlan.totalRuns - replenishPlan.targetRuns === 1 ? "" : "s"}
                    </Badge>
                  </>
                ) : (
                  <Badge variant="outline" className="text-[11px]">
                    No route calculated
                  </Badge>
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <Dialog>
                  <DialogTrigger asChild>
                    <Button type="button" size="sm" variant="outline" disabled={!replenishPlan || isCalculatingRoute}>
                      <Info className="h-3.5 w-3.5" />
                      Reasoning
                    </Button>
                  </DialogTrigger>
                  <DialogContent className="max-w-3xl">
                    <DialogHeader>
                      <DialogTitle>Route Reasoning</DialogTitle>
                      <DialogDescription>
                        Explanation for the last calculated route. Edit inputs, then click Calculate to update this.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="max-h-[65vh] overflow-y-auto pr-2 text-sm">
                      <div className="space-y-3">
                        <div className="rounded-md border bg-muted/20 p-3">
                          <div className="font-medium text-foreground">Optimization goal</div>
                          <div className="mt-1 text-muted-foreground">
                            The planner prioritizes total {plannerInputs.targetDifficulty} runs across the starting energy plus {plannerInputs.refillItems.toLocaleString()} refill item{plannerInputs.refillItems === 1 ? "" : "s"}. Fallback runs are added only after target runs can no longer be improved.
                          </div>
                        </div>
                        <div className="rounded-md border bg-muted/20 p-3">
                          <div className="font-medium text-foreground">Resource model</div>
                          <div className="mt-1 text-muted-foreground">
                            Max energy comes from storage units plus townhall stamina cap. Mine and townhall pump energy is finite collectable energy; collected amounts reduce that source for later cycles.
                          </div>
                        </div>
                        <div className="space-y-2">
                          {replenishPlan && replenishPlan.cycles.map((cycle) => {
                            const cycleLabel = cycle.refillItemUsed === null ? "Start" : `Refill ${cycle.refillItemUsed}`;
                            const rebuildText = fmtStorageChange(cycle.rebuiltEnergyStorehouses, cycle.rebuiltHgEnergyStorehouses);
                            return (
                              <div key={`reason-${cycle.cycleNumber}`} className="rounded-md border p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <div className="font-medium text-foreground">{cycleLabel}</div>
                                  <Badge variant="secondary" className="text-[10px]">
                                    {fmtRunSummary(cycle.targetRuns, cycle.totalRuns)}
                                  </Badge>
                                </div>
                                <div className="mt-2 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                                  <div>Start: {fmtEnergy(cycle.startingEnergy)} / {cycle.startingMaxEnergy.toLocaleString()}</div>
                                  <div>Leftover: {fmtEnergy(cycle.leftoverEnergy)}</div>
                                  {cycle.cycleNumber !== replenishPlan.cycles.length && (
                                    <>
                                      <div>Rebuild chosen: {rebuildText}</div>
                                      <div>Next refill cap: {cycle.restoredMaxEnergy.toLocaleString()}</div>
                                    </>
                                  )}
                                </div>
                                {cycle.cycleNumber !== replenishPlan.cycles.length && (
                                  <div className="mt-2 text-xs text-muted-foreground">
                                    This rebuild is the smallest candidate the planner found that preserves the next-cycle result: {fmtRunSummary(cycle.repeatTargetRuns, cycle.repeatTotalRuns)}.
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  </DialogContent>
                </Dialog>
                <Button
                  type="button"
                  size="sm"
                  onClick={calculateRoute}
                  disabled={isCalculatingRoute}
                  className="transition-transform duration-100 active:scale-95 active:shadow-inner"
                >
                  <Calculator className="h-3.5 w-3.5" />
                  {isCalculatingRoute ? "Calculating..." : "Calculate"}
                </Button>
              </div>
            </div>
            <div className="mt-2 text-muted-foreground">
              {replenishPlan ? `Leftover after route: ${fmtEnergy(replenishPlan.leftoverEnergy)}` : "Click Calculate to build the route."}
              {plannerHasPendingChanges && (
                <span className="ml-2 text-amber-600 dark:text-amber-400">
                  Inputs changed. Click Calculate.
                </span>
              )}
              {routeError && (
                <span className="ml-2 text-destructive">
                  {routeError}
                </span>
              )}
            </div>
            {isCalculatingRoute && (
              <div className="mt-3 rounded-md border border-primary/20 bg-primary/5 p-3 text-xs">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-center gap-2 font-medium text-foreground">
                    <Loader2 className="h-4 w-4 animate-spin text-primary" />
                    Calculating route in the background
                  </div>
                  <div className="font-mono text-muted-foreground">
                    {fmtDuration(routeCalculationSeconds)} elapsed
                  </div>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-primary/15">
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
                </div>
                <div className="mt-2 text-muted-foreground">
                  You can leave the inputs alone while the worker searches for the best route.
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="rounded-md border bg-muted/20 px-3 py-2 space-y-1 text-xs text-muted-foreground">
          <div>
            <span className="font-medium text-foreground">Energy / run formula: </span>
            <span className="font-mono">base x max(50 x mines, max_energy) / 100</span>
          </div>
          <div>
            Current planner multiplier:{" "}
            <span className="font-mono">
              {Math.max(50 * plannerInputs.mines, plannerInputs.maxEnergy).toLocaleString()} / 100 = {(Math.max(50 * plannerInputs.mines, plannerInputs.maxEnergy) / 100).toFixed(2)}
            </span>
            {50 * plannerInputs.mines > plannerInputs.maxEnergy ? (
              <span className="ml-2 text-amber-600 dark:text-amber-400">mines are dominating</span>
            ) : (
              <span className="ml-2">(max energy cap wins)</span>
            )}
          </div>
          {50 * plannerInputs.mines <= plannerInputs.maxEnergy && (
            <div>
              Mine count will not change Wairo cost at this cap until Energy mines reaches{" "}
              <span className="font-mono">{(Math.floor(plannerInputs.maxEnergy / 50) + 1).toLocaleString()}</span>.
            </div>
          )}
        </div>

        {!replenishPlan ? (
          <div className="rounded-md border border-dashed px-3 py-3 text-sm text-muted-foreground">
            {isCalculatingRoute ? `Calculating route... ${fmtDuration(routeCalculationSeconds)} elapsed.` : "No route calculated yet. Click Calculate when the inputs are ready."}
          </div>
        ) : replenishPlan.totalRuns === 0 ? (
          <div className="rounded-md border border-dashed px-3 py-3 text-sm text-muted-foreground">
            No Wairo run is affordable from the current energy and deleteable storage setup.
          </div>
        ) : (
          <div className="space-y-3">
            {replenishPlan.cycles.map((cycle) => {
              const cycleLabel = cycle.refillItemUsed === null ? "Start" : `Refill ${cycle.refillItemUsed}`;
              const fallbackRuns = cycle.totalRuns - cycle.targetRuns;
              return (
                <div key={cycle.cycleNumber} className="overflow-hidden rounded-md border bg-background/40">
                  <div className="flex flex-col gap-2 border-b bg-muted/20 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <div className="text-sm font-semibold text-foreground">{cycleLabel}</div>
                      <div className="text-[11px] text-muted-foreground">
                        Starts at {fmtEnergy(cycle.startingEnergy)} / {cycle.startingMaxEnergy.toLocaleString()} energy
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline" className="text-[10px]">
                        {cycle.targetRuns} {plannerInputs.targetDifficulty}
                      </Badge>
                      <Badge variant="outline" className="text-[10px]">
                        {fallbackRuns} fallback
                      </Badge>
                      <Badge variant="secondary" className="text-[10px]">
                        leftover {fmtEnergy(cycle.leftoverEnergy)}
                      </Badge>
                    </div>
                  </div>
                  <div className="space-y-2 p-3">
                    {cycle.steps.map((step, index) => {
                      const deleteText = fmtStorageChange(
                        step.newlyDeletedEnergyStorehouses,
                        step.newlyDeletedHgEnergyStorehouses,
                      );
                      return (
                        <div key={`${cycle.cycleNumber}-${step.difficulty}-${index}`} className="rounded-md border bg-background/50 p-3">
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <div className="text-xs font-semibold text-foreground">Step {index + 1}</div>
                            <div className="flex flex-wrap justify-end gap-2 text-[11px] text-muted-foreground">
                              <span>Cap {step.maxEnergy.toLocaleString()}</span>
                              <span>Before {fmtEnergy(step.energyBeforeRun)}</span>
                              <span>Cost {fmtEnergy(step.energyCost)}</span>
                              <span className="font-medium text-foreground">After {fmtEnergy(step.energyAfterRun)}</span>
                            </div>
                          </div>
                          <div className="space-y-2">
                            {step.collectActions.length > 0 && (
                              <div className="flex gap-2 rounded-md border border-blue-500/20 bg-blue-500/5 px-2 py-2">
                                <ActionIcon className="text-blue-600 dark:text-blue-400">
                                  <img
                                    src={STAMINA_COLLECT_BUTTON_ICON}
                                    alt=""
                                    className="h-full w-auto min-w-[40px] object-cover object-center"
                                    style={{ imageRendering: "pixelated" }}
                                  />
                                </ActionIcon>
                                <div>
                                  <div className="text-xs font-medium text-foreground">Collect energy</div>
                                  <div className="text-[11px] text-muted-foreground">
                                    {fmtCollectActions(step.collectActions)}
                                  </div>
                                </div>
                              </div>
                            )}
                            {deleteText !== "none" && (
                              <div className="flex gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-2 py-2">
                                <ActionIcon className="text-amber-600 dark:text-amber-400">
                                  <img
                                    src={DESTRUCT_BUTTON_ICON}
                                    alt=""
                                    className="h-full w-auto min-w-[40px] object-cover object-center"
                                    style={{ imageRendering: "pixelated" }}
                                  />
                                </ActionIcon>
                                <div>
                                  <div className="text-xs font-medium text-foreground">Delete storage</div>
                                  <div className="text-[11px] text-muted-foreground">{deleteText}</div>
                                </div>
                              </div>
                            )}
                            <div className="flex gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-2 py-2">
                              <ActionIcon className="text-red-600 dark:text-red-400">
                                <img
                                  src={CONQUEST_MEMBER_ICON}
                                  alt=""
                                  className="h-full w-auto min-w-[40px] object-cover object-center"
                                  style={{ imageRendering: "pixelated" }}
                                />
                              </ActionIcon>
                              <div>
                                <div className="text-xs font-medium text-foreground">Battle</div>
                                <div className="text-[11px] text-muted-foreground">Run {step.difficulty}</div>
                              </div>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                    {cycle.cycleNumber !== replenishPlan.cycles.length && (
                      <div className="rounded-md border bg-muted/10 p-3">
                        <div className="mb-2 text-xs font-semibold text-foreground">Prepare next refill</div>
                        <div className="space-y-2">
                          <div className="flex gap-2 rounded-md border border-green-500/20 bg-green-500/5 px-2 py-2">
                            <ActionIcon className="text-green-600 dark:text-green-400">
                              <img src={MENU_BUILD_ICON} alt="" className="h-full w-auto min-w-[44px] object-cover object-center" style={{ imageRendering: "pixelated" }} />
                            </ActionIcon>
                            <div>
                              <div className="text-xs font-medium text-foreground">Rebuild storage</div>
                              <div className="text-[11px] text-muted-foreground">
                                {fmtStorageChange(cycle.rebuiltEnergyStorehouses, cycle.rebuiltHgEnergyStorehouses)}
                              </div>
                            </div>
                          </div>
                          <div className="flex gap-2 rounded-md border border-primary/20 bg-primary/5 px-2 py-2">
                            <ActionIcon className="text-primary">
                              <img src={recoverEnergyIcon} alt="" className="h-full w-auto min-w-[40px] object-cover object-center" style={{ imageRendering: "pixelated" }} />
                            </ActionIcon>
                            <div>
                              <div className="text-xs font-medium text-foreground">Use refill item</div>
                              <div className="text-[11px] text-muted-foreground">
                                Fill to {cycle.restoredMaxEnergy.toLocaleString()} max energy
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {replenishPlan && (
          <div className="text-[11px] text-muted-foreground">
            Route includes starting energy plus {plannerInputs.refillItems.toLocaleString()} refill item{plannerInputs.refillItems === 1 ? "" : "s"}: {replenishPlan.targetRuns.toLocaleString()} {plannerInputs.targetDifficulty} run{replenishPlan.targetRuns === 1 ? "" : "s"} and {(replenishPlan.totalRuns - replenishPlan.targetRuns).toLocaleString()} fallback run{replenishPlan.totalRuns - replenishPlan.targetRuns === 1 ? "" : "s"}.
          </div>
        )}
      </CardContent>
    </Card>
    </div>
  );
}
