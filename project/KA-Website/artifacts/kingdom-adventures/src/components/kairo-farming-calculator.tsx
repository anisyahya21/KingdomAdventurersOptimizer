import { useEffect, useMemo, useRef, useState } from "react";
import { Calculator, Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ThemedNumberInput } from "@/components/ui/themed-number-input";
import { matchesLooseSearch } from "@/lib/search-normalize";
import { KAIRO_ROOM_LOOT_GROUPS } from "@/lib/special-boss-loot";
import { KAIRO_ROOM_DRAFTS } from "@/lib/en-event-drafts";
import { kairoRunEnergy, encounterExpectedDrops, getAllKairoItems, type Difficulty } from "@/lib/farming-calc";
import { useEquipmentIcons } from "@/hooks/use-equipment-icons";
import { getEquipmentIcon } from "@/lib/equipment-icons";

const WEEKDAY_BY_TITLE = new Map(
  KAIRO_ROOM_DRAFTS.filter((e) => e.active && e.questName).map((e) => [
    e.questName!.replace("'s Challenge", ""),
    e.day,
  ]),
);

const ALL_ITEMS = getAllKairoItems();

type ResultRow = {
  boss: string;
  day: string;
  difficulty: Difficulty;
  energyPerRun: number;
  expectedPerRun: number;
  energyPerItem: number;
  runsForN: number;
  totalEnergy: number;
};

export function KairoFarmingCalculator({ currentDay }: { currentDay: string }) {
  const equipIcons = useEquipmentIcons();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [fighters, setFighters] = useState(6);
  const [desired, setDesired] = useState(1);
  const [todayOnly, setTodayOnly] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filteredItems = useMemo(
    () => (query.trim() ? ALL_ITEMS.filter((i) => matchesLooseSearch(i, query)) : ALL_ITEMS),
    [query],
  );
  const selectedIcon = getEquipmentIcon(equipIcons, selectedItem);

  function selectItem(item: string) {
    setSelectedItem(item);
    setQuery(item);
    setOpen(false);
  }

  const results = useMemo((): ResultRow[] => {
    if (!selectedItem) return [];
    const rows: ResultRow[] = [];
    for (const group of KAIRO_ROOM_LOOT_GROUPS) {
      const day = WEEKDAY_BY_TITLE.get(group.title) ?? "Unknown";
      if (todayOnly && day !== currentDay) continue;
      for (const enc of group.encounters) {
        const energyPerRun = kairoRunEnergy(enc.difficulty, fighters);
        const expectedPerRun = encounterExpectedDrops(enc, selectedItem);
        if (expectedPerRun <= 0) continue;
        const energyPerItem = energyPerRun / expectedPerRun;
        const runsForN = Math.ceil(desired / expectedPerRun);
        rows.push({
          boss: group.title,
          day,
          difficulty: enc.difficulty,
          energyPerRun,
          expectedPerRun,
          energyPerItem,
          runsForN,
          totalEnergy: runsForN * energyPerRun,
        });
      }
    }
    return rows.sort((a, b) => a.energyPerItem - b.energyPerItem);
  }, [selectedItem, fighters, desired, todayOnly, currentDay]);

  const best = results[0] ?? null;

  return (
    <Card id="kairo-farming-calc" className="shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Calculator className="w-4 h-4 text-primary" />
          Farming Calculator
        </CardTitle>
        <CardDescription>
          Expected energy cost to farm a specific drop. Assumes 50/50 table selection each run.
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
                aria-controls="kairo-item-results"
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
                  id="kairo-item-results"
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
              Fighters sent
            </label>
            <ThemedNumberInput value={fighters} min={1} max={40} onValueChange={setFighters} />
            <div className="text-[10px] text-muted-foreground">1 – 40</div>
          </div>

          <div className="space-y-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Items wanted
            </label>
            <ThemedNumberInput value={desired} min={1} onValueChange={setDesired} />
          </div>

          <div className="space-y-1">
            <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Day filter
            </label>
            <div className="flex gap-2">
              <Button
                type="button"
                variant={!todayOnly ? "default" : "outline"}
                size="sm"
                className="h-9 flex-1"
                onClick={() => setTodayOnly(false)}
              >
                All days
              </Button>
              <Button
                type="button"
                variant={todayOnly ? "default" : "outline"}
                size="sm"
                className="h-9 flex-1"
                onClick={() => setTodayOnly(true)}
              >
                Today
              </Button>
            </div>
          </div>
        </div>

        {/* Results */}
        {!selectedItem ? (
          <div className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
            Select a target item above to see farming efficiency across Kairo Room encounters.
          </div>
        ) : results.length === 0 ? (
          <div className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
            {todayOnly
              ? `${selectedItem} does not drop from today's Kairo Room boss.`
              : `${selectedItem} does not drop in any Kairo Room encounter.`}
          </div>
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-xs">
              <thead className="bg-muted/30 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Boss</th>
                  <th className="px-3 py-2 text-left font-medium">Day</th>
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
                      key={`${row.boss}-${row.difficulty}`}
                      className={`border-t border-border/60 ${isBest ? "bg-green-500/10" : ""}`}
                    >
                      <td className="px-3 py-2 font-medium text-foreground">
                        <div className="flex items-center gap-2 flex-wrap">
                          {row.boss}
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
                      <td className="px-3 py-2 text-muted-foreground">{row.day}</td>
                      <td className="px-3 py-2">
                        <Badge variant="secondary" className="text-[11px]">
                          {row.difficulty}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {row.energyPerRun.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {row.expectedPerRun >= 0.001
                          ? row.expectedPerRun.toFixed(4)
                          : row.expectedPerRun.toExponential(2)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right tabular-nums font-semibold ${
                          isBest
                            ? "text-green-600 dark:text-green-400"
                            : "text-foreground"
                        }`}
                      >
                        {Math.round(row.energyPerItem).toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {row.runsForN.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {row.totalEnergy.toLocaleString()}
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
            Energy / run = difficulty base cost + 8 × fighters. Each run picks one of two loot tables at random (50/50 assumed). "Avg / run" is the expected item count averaging both tables.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
