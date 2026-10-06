import { useState } from "react";
import { useLocalFeature } from "@/hooks/sync/use-local-feature";
import { Home } from "lucide-react";
import { useEffect, useMemo } from "react";
import { Link, useSearch } from "wouter";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { CategoryBadge } from "@/components/ka/category-badge";
import { CostPills } from "@/components/ka/cost-pills";
import { DataCard } from "@/components/ka/data-card";
import { EntityLink } from "@/components/ka/entity-link";
import { FilterBar } from "@/components/ka/filter-bar";
import { PageHeader } from "@/components/ka/page-header";
import { PlotPreview } from "@/components/ka/plot-preview";
import { StatTable, StatTableHeaderCell } from "@/components/ka/stat-table";
import { MaterialIcon } from "@/lib/material-icons";
import { getFacilityIcon, getFacilityIconByName, getFurnitureIcon, getItemIcon } from "@/lib/equipment-icons";
import { formatBuildingJobOwners } from "@/game-data/job-buildings";
import {
  BUILDING_GROUP_LABEL,
  BUILDINGS,
  PLOT_SIZES,
  PLOT_TILES,
  type Building,
} from "@/game-data/buildings";
import {
  FACILITIES,
  FACILITY_TABS,
  type Facility,
  type FacilityTab,
} from "@/game-data/facilities";
import { KA_CATEGORY_BADGE_CLASS, KA_FACILITY_TAB_BADGE_CLASS } from "@/design-system/category-styles";
import {
  EQUIPMENT_EXCHANGE_ENTRIES,
  EQUIPMENT_EXCHANGE_OUTPUTS,
  KairoEquipmentName,
  rankLabel,
} from "@/lib/equipment-exchange";

// -- Helpers -------------------------------------------------------------------

type PageTab = "houses" | "facilities";
const PAGE_TABS: { key: PageTab; label: string }[] = [
  { key: "houses",     label: "Houses & Plots" },
  { key: "facilities", label: "Facilities" },
];

// -- Facilities ----------------------------------------------------------------

const FACILITY_PAGE_ROUTES: Partial<Record<number, string>> = {
  168: "/weekly-conquest",
  174: "/job-center",
  180: "/kairo-room",
  200: "/equipment-exchange",
};

function SlotRow({ label, values, highlight }: { label: string; values: [number,number,number,number]; highlight?: boolean }) {
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

function slotKey(b: Building) {
  return b.group + "|" + JSON.stringify([b.beds, b.store, b.monster]);
}

function resolveCardIcon(name: string, facilityId?: number): string | undefined {
  if (typeof facilityId === "number") {
    const fromFacility = getFacilityIcon(facilityId);
    if (fromFacility) return fromFacility;
  }
  return getFacilityIconByName(name) ?? getFurnitureIcon(name) ?? getItemIcon(name);
}

function cardIconClass(icon: string | undefined): string {
  if (icon?.includes("/website_icons/facilities_assembled/")) return "h-11 w-11";
  if (typeof icon === "string" && icon.includes("/website_icons/furniture/")) {
    return "h-11 w-11";
  }
  if (typeof icon === "string" && icon.includes("/website_icons/facilities_confirmed/")) {
    return "h-9 w-9";
  }
  return "h-5 w-5";
}

// The card stays the same width; artwork and details share its available space.
function FacilityCardHeading({ f, icon, children }: {
  f: Facility; icon: string | undefined; children?: React.ReactNode;
}) {
  const large = f.id === 17 || /[4-9]×[4-9]/.test(f.size);
  return (
    <div className={`grid items-center gap-3 ${icon ? large ? "grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]" : "grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]" : "grid-cols-1"}`}>
      {icon && (
        <img src={icon} alt="" className={`w-full object-contain ${large ? "h-[190px]" : "h-28"}`}
          style={{ imageRendering: "pixelated" }} />
      )}
      <div className="min-w-0 space-y-2">
        <span className="block text-[15px] leading-tight text-foreground break-words">{f.name}</span>
        {f.size && <span className="block text-[11px] font-normal text-muted-foreground">Size {f.size}</span>}
        {children}
      </div>
    </div>
  );
}

function BuildingName({ building }: { building: Building }) {
  const jobName = formatBuildingJobOwners(building.name);

  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5">
      <EntityLink type="building" name={building.name} className="text-foreground">
        {building.name}
      </EntityLink>
      {jobName && <span className="text-[11px] font-normal text-muted-foreground">({jobName})</span>}
    </span>
  );
}

function BuildingGroupCard({ buildings }: { buildings: Building[] }) {
  const rep = buildings[0];
  const merged = buildings.length > 1;
  const icon = resolveCardIcon(rep.name);
  const titleNode = (
    <span className="inline-flex items-center gap-2">
      {icon ? <img src={icon} alt="" className={`${cardIconClass(icon)} shrink-0 object-contain`} style={{ imageRendering: "pixelated" }} /> : null}
      {merged ? BUILDING_GROUP_LABEL[rep.group] : <BuildingName building={rep} />}
    </span>
  );
  return (
    <DataCard
      title={titleNode}
      action={<CategoryBadge category={rep.group}>{BUILDING_GROUP_LABEL[rep.group]}</CategoryBadge>}
      contentClassName="space-y-3"
    >
      <PlotPreview buildings={buildings} />
      {merged ? (
        <div className="space-y-1">
          {buildings.map(b => (
            <div key={b.id} className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium">
                <BuildingName building={b} />
              </span>
              <CostPills costs={b} />
            </div>
          ))}
        </div>
      ) : (
        <CostPills costs={rep} />
      )}
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
          <SlotRow label="Extra beds"   values={rep.beds}    highlight />
          {rep.store.some(v => v > 0) && (
            <SlotRow label="Shelves"    values={rep.store}   highlight />
          )}
          {rep.monster.some(v => v > 0) && (
            <SlotRow label="Monster"    values={rep.monster} />
          )}
        </tbody>
      </StatTable>
    </DataCard>
  );
}

// itemGroup per upgradeable facility (col96 from Facility_lookup.csv)
// -1 / absent = no item costs; 0 = Town Hall (handled by TownHallCard)
const FACILITY_ITEM_GROUP: Record<number, number> = {
  7:2, 17:0, 23:3, 24:3, 25:3, 26:3, 28:3, 29:4, 30:4, 31:4, 32:4,
  33:5, 34:5, 35:5, 36:5, 37:5, 38:5, 39:5, 40:5, 41:5, 42:6, 43:6,
  44:6, 45:6, 46:6, 47:6, 66:7, 67:7, 75:7, 76:7, 77:7, 78:7, 79:7,
  80:7, 81:7, 82:7, 83:7, 84:7, 85:7, 86:7, 87:7, 88:7, 89:7, 90:7,
  91:7, 92:7, 106:8, 107:9, 108:9, 109:9, 110:9, 111:9, 112:9, 113:9,
  114:9, 115:9, 116:9, 117:9, 118:9, 119:8, 120:8, 121:8, 122:8, 123:8,
  124:8, 125:8, 126:8, 128:8, 132:8, 133:8, 134:8, 135:8, 136:8, 137:8,
  139:8, 140:8, 141:8, 142:8, 145:8, 146:8, 147:8, 148:8, 150:8, 151:8,
  153:8, 158:9, 159:7, 161:7, 183:7, 184:7, 185:7, 186:7, 192:7, 194:5,
  206:5, 207:5, 208:5, 209:5, 210:5, 211:5, 212:5, 213:5, 214:5, 223:8,
  224:9, 225:9, 229:8, 236:8, 237:8, 239:2, 241:7,
};

// Item names for each group's item list
// Groups 0/2/3/5/6/7/9: 4 items always, qty = max(1, floor(N/3)) × ratios 3:2:1:3
// Groups 4/8: progressive unlock (1 + floor((N-1)/3) items), each qty=1
type ItemGroupDef =
  | { kind: "scaled"; items: [string, number][] }   // [name, ratio]
  | { kind: "progressive"; items: string[] };        // unlocked in order

const ITEM_GROUP_DEF: Record<number, ItemGroupDef> = {
  0: { kind: "scaled",      items: [["Sturdy Board",3],["Large Nail",2],["Strong Rope",1],["Copper Coin",3]] },
  2: { kind: "scaled",      items: [["Strong Rope",3],["High Grade Brick",2],["Silk Cloth",1],["Copper Coin",3]] },
  3: { kind: "scaled",      items: [["Sturdy Board",3],["Iron Ore",2],["High Grade Brick",1],["Copper Coin",3]] },
  5: { kind: "scaled",      items: [["High Grade Brick",3],["Strong Cloth",2],["Bronze",1],["Copper Coin",3]] },
  6: { kind: "scaled",      items: [["Strong Rope",3],["Large Nail",2],["Gold Nugget",1],["Copper Coin",3]] },
  7: { kind: "scaled",      items: [["High Grade Brick",3],["Strong Rope",2],["Silk Cloth",1],["Copper Coin",3]] },
  9: { kind: "scaled",      items: [["Large Nail",3],["Iron Ore",2],["Pretty Cloth",1],["Copper Coin",3]] },
  4: { kind: "progressive", items: ["Strong Rope","Silk Cloth"] },
  8: { kind: "progressive", items: ["Large Nail","Sturdy Board","Strong Cloth"] },
};

function calcItemCosts(facilityId: number, N: number): { name: string; qty: number }[] {
  const groupId = FACILITY_ITEM_GROUP[facilityId];
  if (groupId == null || groupId === 0) return [];
  const def = ITEM_GROUP_DEF[groupId];
  if (!def) return [];
  if (def.kind === "scaled") {
    const q = Math.max(1, Math.floor(N / 3));
    return def.items.map(([name, ratio]) => ({ name, qty: q * ratio }));
  } else {
    const count = 1 + Math.floor((N - 1) / 3);
    return def.items.slice(0, count).map(name => ({ name, qty: 1 }));
  }
}

function calcTownHallCoinCosts(nextRank: number): { name: string; qty: number }[] {
  const q = Math.max(1, Math.floor(nextRank / 3));
  return [
    { name: "Copper Coin", qty: q * 3 },
  ];
}

function calcTownHallMaterialCosts(nextRank: number): { name: string; qty: number }[] {
  const q = Math.max(1, Math.floor(nextRank / 3));
  return [
    { name: "Sturdy Board", qty: q * 3 },
    { name: "Large Nail", qty: q * 2 },
    { name: "Strong Rope", qty: q * 1 },
  ];
}

function FacilityItemCost({ name, qty }: { name: string; qty: number }) {
  const icon = getItemIcon(name);
  return (
    <span title={name} className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      {icon && <img src={icon} alt={name} width={24} height={24} style={{ imageRendering: "pixelated", objectFit: "contain" }} />}
      {qty}×
    </span>
  );
}

// What a facility gains from leveling up
type FacilityGain = "mine" | "farm" | "shop" | "port" | "exp" | "hp";

const FACILITY_GAIN: Partial<Record<number, FacilityGain>> = {
  // Mines / production (outdoor)
  45:"mine", 46:"mine", 47:"mine",
  194:"mine", 241:"mine",
  // Field / Plantation (more harvests per planting)
  42:"farm", 43:"farm",
  // Indoor production items
  119:"mine", 120:"mine", 121:"mine", 122:"mine", 123:"mine", 124:"mine",
  127:"mine", 136:"mine", 137:"mine", 138:"mine", 139:"mine", 142:"mine",
  146:"mine", 152:"mine",
  // Shops / workbenches / shelves / registers
  102:"shop", 103:"shop", 104:"shop", 105:"shop",
  110:"shop", 111:"shop", 112:"shop", 113:"shop", 114:"shop",
  115:"shop", 116:"shop", 117:"shop", 118:"shop",
  193:"shop", 234:"shop",
  // Ports & airports
  7:"port", 10:"port", 239:"port",
  // EXP furniture & training
  75:"exp", 106:"exp", 126:"exp", 128:"exp",
  132:"exp", 133:"exp", 134:"exp",
  141:"exp", 143:"exp", 144:"exp", 145:"exp",
  147:"exp", 148:"exp", 149:"exp", 150:"exp", 151:"exp", 153:"exp",
};

const GAIN_BADGE: Record<FacilityGain, { label: string; cls: string }> = {
  mine: { label: "⛏ Produces faster",        cls: KA_CATEGORY_BADGE_CLASS.shop },
  farm: { label: "🌾 More harvests",           cls: KA_CATEGORY_BADGE_CLASS.success },
  shop: { label: "🛒 More items available",   cls: KA_CATEGORY_BADGE_CLASS.house },
  port: { label: "⏱ Longer visit duration",  cls: KA_CATEGORY_BADGE_CLASS.survey },
  exp:  { label: "⭐ More EXP per use",       cls: KA_CATEGORY_BADGE_CLASS.special },
  hp:   { label: "🛡 Durability only",        cls: KA_CATEGORY_BADGE_CLASS.muted },
};

function facilityGain(id: number, canUpgrade: boolean): FacilityGain | null {
  if (!canUpgrade) return null;
  return FACILITY_GAIN[id] ?? "hp";
}

// From KA GameData - Warehouse.csv, matched to facility ids via Facility_lookup.csv dataId.
const FACILITY_STORAGE_CAPACITY: Partial<Record<number, string>> = {
  // Source notes: see data/sheet-research/facility-notes.md
  // Confirmed in-game behavior:
  // - low-tier storehouses display 4x the raw Warehouse.csv capacity
  // - HG storehouses match Warehouse.csv directly
  // - coin boxes are effectively unlimited for the player
  33: "20",
  34: "20",
  35: "20",
  36: "20",
  37: "20",
  38: "40",
  39: "20",
  40: "16",
  183: "Unlimited",
  184: "Unlimited",
  206: "100",
  207: "100",
  208: "100",
  209: "100",
  210: "100",
  211: "150",
  212: "50",
  213: "300",
  214: "50",
};

const MATERIAL_STOREHOUSE_IDS = new Set([
  33, 34, 35, 36, 37, 38, 39, 40,
  183, 184,
  206, 207, 208, 209, 210, 211, 212, 213, 214,
]);


function applyResourceDiscount(qty: number, resourceDiscount: number): number {
  return Math.max(0, Math.round(qty * (1 - resourceDiscount)));
}

function applyResourceDiscountToItems<T extends { qty: number }>(items: T[], resourceDiscount: number): T[] {
  return items.map((item) => ({
    ...item,
    qty: applyResourceDiscount(item.qty, resourceDiscount),
  }));
}

function formatUpgTime(seconds: number): string {
  if (seconds <= 0) return "0s";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const totalMinutes = Math.round(seconds / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

function formatHoursMinutes(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  if (h <= 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

function calcEnergyMineTimeToFill(level: number): number {
  const clampedLevel = Math.max(1, Math.min(100, level));
  const levelOffset = clampedLevel - 1;
  const capacity = 50 + ((1000 - 50) / 99) * levelOffset;
  const fullTimeSeconds = 6000 + ((60000 - 6000) / 99) * levelOffset;
  const timeTo50Seconds = fullTimeSeconds * 50 / capacity;
  return Math.max(3000, timeTo50Seconds);
}

function MaterialFacilitySection({
  title,
  facilities,
  timeDiscount,
  resourceDiscount,
}: {
  title: string;
  facilities: Facility[];
  timeDiscount: number;
  resourceDiscount: number;
}) {
  if (facilities.length === 0) return null;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-px flex-1 bg-border" />
        <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          {title}
        </h3>
        <div className="h-px flex-1 bg-border" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {facilities.map(f => f.id === 17
          ? <TownHallCard key={f.id} f={f} timeDiscount={timeDiscount} resourceDiscount={resourceDiscount} />
          : <FacilityCard key={f.id} f={f} timeDiscount={timeDiscount} resourceDiscount={resourceDiscount} />
        )}
      </div>
    </div>
  );
}

function FacilityCosts({ g, w, f, o, m }: { g: number; w: number; f: number; o: number; m: number }) {
  const vals = [
    { matId: 0, style: "outlined" as const, v: g },
    { matId: 1, style: "flat"     as const, v: w },
    { matId: 2, style: "flat"     as const, v: f },
    { matId: 3, style: "crystal"  as const, v: o },
    { matId: 4, style: "flat"     as const, v: m },
  ].filter(x => x.v > 0);
  if (vals.length === 0)
    return <span className="text-[11px] text-muted-foreground italic">Free</span>;
  return (
    <div className="flex flex-wrap gap-2 items-center">
      {vals.map(c => (
        <span key={c.matId} title={["Grass","Wood","Food","Ore","Mystic Ore"][c.matId]} className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <MaterialIcon id={c.matId} style={c.style} size={24} />
          {c.v}
        </span>
      ))}
    </div>
  );
}

function EquipmentExchangeCalculator() {
  const [target, setTarget] = useState<KairoEquipmentName>("A/ Kairo Gun");
  const [targetCount, setTargetCount] = useState(10);
  const [sourceQuery, setSourceQuery] = useState("");
  const [currentPrices, setCurrentPrices] = useState<Record<number, number>>({});

  const entries = useMemo(
    () => EQUIPMENT_EXCHANGE_ENTRIES.filter((entry) => entry.outputName === target),
    [target],
  );

  const filteredEntries = useMemo(() => {
    const q = sourceQuery.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((entry) => entry.inputName.toLowerCase().includes(q));
  }, [entries, sourceQuery]);

  const route = useMemo(() => {
    const states = entries.map((entry) => ({
      entry,
      currentExchange: Math.max(0, currentPrices[entry.inputId] ?? entry.startPrice),
      used: 0,
      totalCoin: 0,
      totalExchange: 0,
    }));

    const picks: Array<{ item: string; tradeCost: number; copperCost: number; combinedCost: number }> = [];
    let totalCoin = 0;
    let totalExchange = 0;

    for (let i = 0; i < targetCount; i += 1) {
      let best = states[0];
      let bestCombined = Number.POSITIVE_INFINITY;
      for (const state of states) {
        const combined = state.entry.copperCoinPrice + state.currentExchange;
        if (
          combined < bestCombined ||
          (combined === bestCombined && state.currentExchange < best.currentExchange) ||
          (combined === bestCombined && state.currentExchange === best.currentExchange && state.entry.inputName < best.entry.inputName)
        ) {
          best = state;
          bestCombined = combined;
        }
      }

      best.used += 1;
      best.totalCoin += best.entry.copperCoinPrice;
      best.totalExchange += best.currentExchange;
      totalCoin += best.entry.copperCoinPrice;
      totalExchange += best.currentExchange;
      picks.push({
        item: best.entry.inputName,
        tradeCost: best.currentExchange,
        copperCost: best.entry.copperCoinPrice,
        combinedCost: best.entry.copperCoinPrice + best.currentExchange,
      });
      best.currentExchange += best.entry.priceStep;
    }

    const usedEntries = states
      .filter((state) => state.used > 0)
      .sort((a, b) => {
        if (b.used !== a.used) return b.used - a.used;
        return a.entry.inputName.localeCompare(b.entry.inputName);
      });

    return {
      totalCoin,
      totalExchange,
      totalCombined: totalCoin + totalExchange,
      usedEntries,
      picks,
    };
  }, [currentPrices, entries, targetCount]);

  const setCurrentPrice = (inputId: number, value: number) => {
    setCurrentPrices((prev) => ({ ...prev, [inputId]: Math.max(0, value) }));
  };

  const resetVisible = () => {
    setCurrentPrices((prev) => {
      const next = { ...prev };
      for (const entry of entries) {
        delete next[entry.inputId];
      }
      return next;
    });
  };

  return (
    <div className="space-y-4 rounded-lg border border-teal-500/20 bg-teal-500/5 p-3">
      <div className="space-y-1">
        <h4 className="text-sm font-semibold">Equipment Exchange Calculator</h4>
        <p className="text-xs text-muted-foreground">
          Decoded from the mined exchange table: each tradable equipment item has its own starting exchange price and its own increase per trade.
          The cheapest route can mix different source items, so this calculator picks the lowest live cost step-by-step.
        </p>
      </div>

      <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-950 dark:text-amber-200">
        Enter your current live exchange price for any source item you have checked in-game. If you leave a row untouched, it uses the mined starting price.
        The copper column uses the mined copper coin value from the equipment sheet.
      </div>

      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_120px]">
        <div className="space-y-1">
          <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Target Kairo piece</label>
          <div className="flex flex-wrap gap-2">
            {EQUIPMENT_EXCHANGE_OUTPUTS.map((output) => (
              <button
                key={output}
                type="button"
                onClick={() => setTarget(output)}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                  target === output
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-background hover:bg-muted"
                }`}
              >
                {output}
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-1">
          <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">How many needed</label>
          <Input
            type="number"
            min={1}
            value={targetCount}
            onChange={(e) => setTargetCount(Math.max(1, Number(e.target.value) || 1))}
            className="h-9"
          />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <CardContent className="p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Copper total</div>
            <div className="mt-1 text-lg font-semibold tabular-nums">{route.totalCoin}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Exchange total</div>
            <div className="mt-1 text-lg font-semibold tabular-nums">{route.totalExchange}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Combined total</div>
            <div className="mt-1 text-lg font-semibold tabular-nums">{route.totalCombined}</div>
          </CardContent>
        </Card>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Best route summary</h5>
          <div className="text-xs text-muted-foreground">
            {route.usedEntries.length} source item{route.usedEntries.length === 1 ? "" : "s"} used
          </div>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          {route.usedEntries.map((state) => (
            <div key={state.entry.inputId} className="rounded-md border border-border bg-background/60 px-3 py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{state.entry.inputName}</span>
                <span className="tabular-nums text-muted-foreground">×{state.used}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                <span>Rank {rankLabel(state.entry.inputName)}</span>
                <span>Copper {state.entry.copperCoinPrice}</span>
                <span>Start {state.entry.startPrice}</span>
                <span>+{state.entry.priceStep} each</span>
                <span className="font-medium text-foreground">Total {state.totalCoin + state.totalExchange}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <details className="rounded-md border border-border bg-background/40">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-foreground">
          Show per-trade route
        </summary>
        <div className="border-t border-border px-3 py-2">
          <div className="grid gap-1">
            {route.picks.map((pick, index) => (
              <div key={`${pick.item}-${index}`} className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate">
                  {index + 1}. {pick.item}
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  copper {pick.copperCost} + exchange {pick.tradeCost} = {pick.combinedCost}
                </span>
              </div>
            ))}
          </div>
        </div>
      </details>

      <div className="space-y-2">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Source items</h5>
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
              Reset current prices
            </button>
          </div>
        </div>

        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[680px] border-collapse text-xs">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Item</th>
                <th className="px-3 py-2 text-left font-medium">Rank</th>
                <th className="px-3 py-2 text-right font-medium">Copper</th>
                <th className="px-3 py-2 text-right font-medium">Mined start</th>
                <th className="px-3 py-2 text-right font-medium">Step</th>
                <th className="px-3 py-2 text-right font-medium">Your current trade</th>
                <th className="px-3 py-2 text-right font-medium">Next total</th>
              </tr>
            </thead>
            <tbody>
              {filteredEntries.map((entry) => {
                const current = currentPrices[entry.inputId] ?? entry.startPrice;
                return (
                  <tr key={entry.inputId} className="border-t border-border">
                    <td className="px-3 py-2 font-medium">{entry.inputName}</td>
                    <td className="px-3 py-2 text-muted-foreground">{rankLabel(entry.inputName)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{entry.copperCoinPrice}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{entry.startPrice}</td>
                    <td className="px-3 py-2 text-right tabular-nums">+{entry.priceStep}</td>
                    <td className="px-3 py-2">
                      <Input
                        type="number"
                        min={0}
                        value={current}
                        onChange={(e) => setCurrentPrice(entry.inputId, Number(e.target.value) || 0)}
                        className="h-8 text-right tabular-nums"
                      />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{entry.copperCoinPrice + current}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function FacilityCard({ f, timeDiscount = 0, resourceDiscount = 0 }: { f: Facility; timeDiscount?: number; resourceDiscount?: number }) {
  const [level, setLevel] = useState(0);
  const MAX_LEVEL = 99; // max upgrade level is 100; slider 0..99 = lv1..lv100
  const displayLevel = level + 1;
  const facilityRoute = FACILITY_PAGE_ROUTES[f.id];
  const icon = resolveCardIcon(f.name, f.id);

  function interp(lv1: number, maxV: number) {
    if (lv1 === 0 && maxV === 0) return 0;
    return lv1 + Math.floor((maxV - lv1) * level / MAX_LEVEL);
  }

  const uG = Math.round(interp(f.upgGrass,   f.maxUpgGrass)  * (1 - resourceDiscount));
  const uW = Math.round(interp(f.upgWood,    f.maxUpgWood)   * (1 - resourceDiscount));
  const uF = Math.round(interp(f.upgFood,    f.maxUpgFood)   * (1 - resourceDiscount));
  const uO = Math.round(interp(f.upgOre,     f.maxUpgOre)    * (1 - resourceDiscount));
  const uM = Math.round(interp(f.upgMystic,  f.maxUpgMystic) * (1 - resourceDiscount));

  const minTime = Math.round(120 * (1 - timeDiscount));
  const maxTime = Math.round(129600 * (1 - timeDiscount));
  const upgTime = Math.round(minTime + (maxTime - minTime) * level / MAX_LEVEL);
  const energyMineTimeToFill = f.id === 47 ? calcEnergyMineTimeToFill(displayLevel) : null;
  const storageCapacity = FACILITY_STORAGE_CAPACITY[f.id] ?? null;

  const hasUpg   = f.canUpgrade && (f.upgGrass > 0 || f.upgWood > 0 || f.upgFood > 0 || f.upgOre > 0 || f.upgMystic > 0
                                  || f.maxUpgGrass > 0 || f.maxUpgWood > 0 || f.maxUpgFood > 0 || f.maxUpgOre > 0 || f.maxUpgMystic > 0);
  const tabLabel = FACILITY_TABS.find(t => t.key === f.tab)?.label ?? f.tab;

  return (
    <DataCard
      titleClassName="w-full min-w-0"
      title={
        <FacilityCardHeading f={f} icon={icon}>
          {facilityRoute && (
            <Link href={facilityRoute}>
              <Badge variant="outline" className="cursor-pointer text-[10px] bg-primary/10 text-primary border-primary/30 hover:bg-primary/15">
                Open page
              </Badge>
            </Link>
          )}
        </FacilityCardHeading>
      }
      meta={(f.canUpgrade || f.validRange > 0 || f.mapUnlock !== undefined) && (
          <div className="flex flex-wrap gap-1 mt-1">
            {f.size && (
              <Badge variant="outline" className="text-[10px] tabular-nums font-mono">
                {f.size}{f.rotatable ? " 🔄" : ""}
              </Badge>
            )}
            {f.mapUnlock !== undefined && (
              <Badge variant="outline" className={`text-[10px] tabular-nums ${KA_CATEGORY_BADGE_CLASS.facility}`}>
                Map Lv.{f.mapUnlock}
              </Badge>
            )}
            {f.canUpgrade && (
              <Badge variant="outline" className={`text-[10px] ${KA_CATEGORY_BADGE_CLASS.success}`}>
                Upgradeable
              </Badge>
            )}
            {(() => {
              const gain = facilityGain(f.id, f.canUpgrade);
              if (!gain) return null;
              const { label, cls } = GAIN_BADGE[gain];
              return <Badge variant="outline" className={`text-[10px] ${cls}`}>{label}</Badge>;
            })()}
            {f.validRange > 0 && (
              <Badge variant="outline" className="text-[10px] tabular-nums">
                📍 {f.validRange} tiles
              </Badge>
            )}
          </div>
        )}
      contentClassName="space-y-2"
    >
        {f.minHp > 0 && (
          <p className="text-xs text-muted-foreground">HP {f.minHp}–{f.maxHp}</p>
        )}
        {f.minUseCount != null && f.maxUseCount != null && (
          <p className="text-xs text-muted-foreground">
            🌾 Harvests per planting: <span className="font-medium text-foreground">
              {f.minUseCount + Math.floor((f.maxUseCount - f.minUseCount) * level / MAX_LEVEL)}
            </span>
          </p>
        )}
        {energyMineTimeToFill != null && (
          <p className="text-xs text-muted-foreground">
            ⚡ Time to fill: <span className="font-medium text-foreground">{formatHoursMinutes(energyMineTimeToFill)}</span>
          </p>
        )}
        {storageCapacity != null && (
          <p className="text-xs text-muted-foreground">
            📦 Capacity: <span className="font-medium text-foreground">{storageCapacity}</span>
          </p>
        )}
        {f.id === 191 && (
          <div className="rounded-md border border-red-300 bg-red-50/70 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-300">
            <strong className="text-foreground dark:text-red-200">Warning:</strong> when removing a Chaos Stone, pay the 50 diamonds or it will be lost permanently. Losing one permanently is bad enough that many players would rather restart than accept it.
          </div>
        )}
        {hasUpg && (
          <div className="space-y-1 border-t border-border pt-2 mt-1">
            <div className="flex items-center justify-between gap-2">
              <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground/70 uppercase tracking-wide font-medium">
                <img src={RANK_UP_BUTTON_ICON} alt="" className="h-3.5 w-3.5 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                Upgrade cost (Lv. {level + 1}{" -> "}{level + 2})
              </p>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={() => setLevel(l => Math.max(0, l - 1))}
                  disabled={level === 0}
                  className="w-5 h-5 rounded text-xs font-bold border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >-</button>
                <span className="text-xs tabular-nums w-7 text-center font-medium">{level + 1}</span>
                <button
                  onClick={() => setLevel(l => Math.min(MAX_LEVEL, l + 1))}
                  disabled={level === MAX_LEVEL}
                  className="w-5 h-5 rounded text-xs font-bold border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >+</button>
              </div>
            </div>
            <FacilityCosts g={uG} w={uW} f={uF} o={uO} m={uM} />
            {(() => {
              const items = calcItemCosts(f.id, level + 1);
              if (items.length === 0) return null;
              return (
                <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                  {items.map(({ name, qty }) => (
                    <FacilityItemCost key={name} name={name} qty={qty} />
                  ))}
                </div>
              );
            })()}
            <p className="text-xs font-medium text-amber-600 dark:text-amber-400">⏱ {formatUpgTime(upgTime)}</p>
          </div>
        )}
    </DataCard>
  );
}

// -- Town Hall rank-aware card -------------------------------------------------
// Upgrade time (seconds) to reach rank N+1, indexed 0..99
// (i.e. TH_UPGRADE_TIMES[0] = time to go rank 0?1, [99] = rank 99?100)
const TH_UPGRADE_TIMES = [
    5,   120,   300,   420,   600,   900,  1200,  1800,  2700,  3600,
 7200, 10800, 14400, 18000, 21600, 25200, 28800, 32400, 36000, 39600,
43200, 46800, 50400, 54000, 57600, 61200, 64800, 68400, 72000, 75600,
79200, 82800, 86400, 90000, 93600, 97200,100800,104400,108000,111600,
115200,118800,122400,126000,129600,133200,136800,140400,144000,147600,
151200,154800,158400,162000,165600,169200,172800,180000,187200,194400,
201600,208800,216000,223200,230400,237600,244800,252000,259200,259200,
280800,302400,324000,345600,367200,388800,410400,432000,453600,475200,
496800,518400,540000,561600,583200,604800,626400,648000,669600,691200,
712800,734400,756000,777600,799200,820800,842400,864000,885600,907200,
];

const RANK_UP_BUTTON_ICON = "/website_icons/requested/rankup_button.png";

function TownHallCard({ f, timeDiscount = 0, resourceDiscount = 0 }: { f: Facility; timeDiscount?: number; resourceDiscount?: number }) {
  const [rank, setRank] = useState(0);
  const maxRank = 99; // can't upgrade past 100
  const icon = resolveCardIcon(f.name, f.id);

  function interp(minV: number, maxV: number) {
    if (minV === 0 && maxV === 0) return 0;
    return Math.round(minV + (maxV - minV) * rank / 99);
  }

  const uG = Math.round(interp(f.upgGrass, f.maxUpgGrass)  * (1 - resourceDiscount));
  const uW = Math.round(interp(f.upgWood,  f.maxUpgWood)   * (1 - resourceDiscount));
  const uF = Math.round(interp(f.upgFood,  f.maxUpgFood)   * (1 - resourceDiscount));
  const uO = Math.round(interp(f.upgOre,   f.maxUpgOre)    * (1 - resourceDiscount));
  const uM = Math.round(interp(f.upgMystic,f.maxUpgMystic) * (1 - resourceDiscount));
  const upgTime = Math.round(TH_UPGRADE_TIMES[rank] * (1 - timeDiscount));

  return (
    <DataCard
      titleClassName="w-full min-w-0"
      title={
        <FacilityCardHeading f={f} icon={icon}>
          <div className="flex flex-wrap gap-1">
            <Badge variant="outline" className={`text-[10px] ${KA_FACILITY_TAB_BADGE_CLASS[f.tab]}`}>
              {FACILITY_TABS.find(t => t.key === f.tab)?.label}
            </Badge>
            <Badge variant="outline" className={`text-[10px] ${KA_CATEGORY_BADGE_CLASS.success}`}>
              Upgradeable
            </Badge>
            {f.validRange > 0 && <Badge variant="outline" className="text-[10px] tabular-nums">📍 {f.validRange} tiles</Badge>}
          </div>
          {f.minHp > 0 && <p className="text-xs font-normal text-muted-foreground">HP {f.minHp}–{f.maxHp}</p>}
        </FacilityCardHeading>
      }
      contentClassName="space-y-2"
    >
        <div className="space-y-1 border-t border-border pt-2">
          <div className="flex items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground/70 uppercase tracking-wide font-medium">
              <img src={RANK_UP_BUTTON_ICON} alt="" className="h-3.5 w-3.5 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
              Upgrade cost (Rank {rank}{" -> "}{rank + 1})
            </p>
            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={() => setRank(r => Math.max(0, r - 1))}
                disabled={rank === 0}
                className="w-5 h-5 rounded text-xs font-bold border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >-</button>
              <span className="text-xs tabular-nums w-7 text-center font-medium">{rank}</span>
              <button
                onClick={() => setRank(r => Math.min(maxRank, r + 1))}
                disabled={rank === maxRank}
                className="w-5 h-5 rounded text-xs font-bold border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >+</button>
            </div>
          </div>
          <FacilityCosts g={uG} w={uW} f={uF} o={uO} m={uM} />
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            {[...calcTownHallMaterialCosts(rank + 1), ...applyResourceDiscountToItems(calcTownHallCoinCosts(rank + 1), resourceDiscount)].map(({ name, qty }) => (
              <FacilityItemCost key={name} name={name} qty={qty} />
            ))}
          </div>
          <p className="text-xs font-medium text-amber-600 dark:text-amber-400">⏱ {formatUpgTime(upgTime)}</p>
        </div>
    </DataCard>
  );
}

// -- Page ----------------------------------------------------------------------

export default function HousesPage() {
  const search = useSearch();
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<PageTab>("houses");
  const [facilityTab, setFacilityTab] = useState<FacilityTab>("env");
  const [storedKnowHow, setStoredKnowHow] = useLocalFeature<number>("houses-facilities-know-how", 0);
  const [storedCraftsman, setStoredCraftsman] = useLocalFeature<number>("houses-facilities-craftsman", 0);
  const knowHow = Math.max(0, Math.min(6, Number(storedKnowHow) || 0));
  const craftsman = Math.max(0, Math.min(6, Number(storedCraftsman) || 0));
  const setKnowHow = (n: number) => setStoredKnowHow(Math.max(0, Math.min(6, n)));
  const setCraftsman = (n: number) => setStoredCraftsman(Math.max(0, Math.min(6, n)));
  const timeDiscount = knowHow * 0.05;
  const resourceDiscount = craftsman * 0.05;

  useEffect(() => {
    const params = new URLSearchParams(search);
    const nextTab = params.get("tab");
    if (nextTab === "houses" || nextTab === "facilities") {
      setTab(nextTab);
    }
    const nextFacilityTab = params.get("facilityTab");
    if (
      nextFacilityTab === "env" ||
      nextFacilityTab === "materials" ||
      nextFacilityTab === "amenity" ||
      nextFacilityTab === "indoors" ||
      nextFacilityTab === "map"
    ) {
      setFacilityTab(nextFacilityTab);
    }
    const nextQuery = params.get("search");
    if (nextQuery !== null) {
      setQuery(nextQuery);
    }
  }, [search]);

  const q = query.trim().toLowerCase();

  // Buildings are plots. Facilities cover non-plot town infrastructure.
  const filtered = tab === "facilities" ? [] : BUILDINGS.filter(b => {
    if (!q) return true;
    return b.name.toLowerCase().includes(q);
  });

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">

      <PageHeader icon={<Home className="w-5 h-5" />} title="Houses & Facilities">
        <p>
          Plan Kingdom Adventurers houses and facilities with plot sizes, building costs, extra beds,
          shelves, monster room slots, upgrade costs, upgrade time, HP, range, storage, production, and map unlock data.
        </p>
        <p>
          Houses & Plots shows what each S / M / L / XL plot can become and what fits inside. Facilities covers
          town infrastructure like walls, gates, roads, storehouses, resource production, indoor objects, and map-unlocked buildings.
        </p>
      </PageHeader>

      <FilterBar
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder="Search buildings..."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          {PAGE_TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`rounded-xl border px-5 py-4 text-left transition-all ${
                tab === t.key
                  ? "bg-primary text-primary-foreground border-primary shadow-md"
                  : "bg-muted/30 text-foreground border-border hover:bg-muted/60"
              }`}
            >
              <div className="text-base font-semibold">{t.label}</div>
              <div className={`mt-1 text-xs leading-relaxed ${tab === t.key ? "text-primary-foreground/85" : "text-muted-foreground"}`}>
                {t.key === "houses"
                  ? "Plots, building types, extra beds, shelves, monster room slots, and owner jobs."
                  : "Town facilities, indoor objects, map unlocks, upgrade costs, and utility structures."}
              </div>
            </button>
          ))}
        </div>
      </FilterBar>

      {tab === "houses" && <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground border border-border rounded-md px-4 py-3 bg-muted/20">
        <span><span className="font-semibold">Extra beds</span> × additional bed placements beyond the default bed</span>
        <span><span className="font-semibold">Shelves</span> × shop display slots for goods</span>
        <span><span className="font-semibold">Monster</span> × monster room slots</span>
      </div>}

      {tab === "facilities" ? (
        <div className="space-y-4">
          {/* Modifier panel */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border border-border bg-muted/20 px-4 py-2.5">
            <span className="text-[10px] uppercase tracking-wide font-semibold text-orange-500 dark:text-orange-400 shrink-0">Modifiers</span>
            {([
              { label: "Know-How Journal", iconSrc: "/website_icons/valuable/know_how_journal.png", value: knowHow, set: setKnowHow, suffix: "-5% upgrade time each" },
              { label: "Master Craftsman's Tools", iconSrc: "/website_icons/valuable/master_craftsmans_tools.png", value: craftsman, set: setCraftsman, suffix: "-5% resource cost each" },
            ] as { label: string; iconSrc: string; value: number; set: (n: number) => void; suffix: string }[]).map(({ label, iconSrc, value, set, suffix }) => (
              <div key={label} className="flex items-center gap-2">
                <img
                  src={iconSrc}
                  alt=""
                  className="h-6 w-6 shrink-0 object-contain"
                  style={{ imageRendering: "pixelated" }}
                />
                <span className="text-xs text-muted-foreground hidden sm:inline">{label}</span>
                <span className="text-xs text-muted-foreground sm:hidden">{label.split("'")[0].trim()}</span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => set(Math.max(0, value - 1))}
                    disabled={value === 0}
                    className="w-5 h-5 rounded text-xs font-bold border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >-</button>
                  <span className="text-xs tabular-nums w-4 text-center font-medium">{value}</span>
                  <button
                    onClick={() => set(Math.min(6, value + 1))}
                    disabled={value === 6}
                    className="w-5 h-5 rounded text-xs font-bold border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >+</button>
                </div>
                {value > 0 ? (
                  <span className="text-[11px] font-medium text-emerald-600 dark:text-emerald-400">-{value * 5}%</span>
                ) : (
                  <span className="text-[11px] text-muted-foreground/40">{suffix}</span>
                )}
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-1">
            {FACILITY_TABS.map(ft => (
              <button
                key={ft.key}
                onClick={() => setFacilityTab(ft.key)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors border ${
                  facilityTab === ft.key
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-muted/40 text-muted-foreground border-border hover:bg-muted/70"
                }`}
              >
                {ft.label}
              </button>
            ))}
          </div>
          {(() => {
            const inTab = FACILITIES.filter(f => f.tab === facilityTab);
            const sorted = facilityTab === "map"
              ? [...inTab].sort((a, b) => (a.mapUnlock ?? 0) - (b.mapUnlock ?? 0))
              : inTab;
            const shown = q ? sorted.filter(f => f.name.toLowerCase().includes(q)) : sorted;
            if (shown.length === 0)
              return (
                <div className="py-12 text-center">
                  <p className="text-sm text-muted-foreground">
                    {q ? `No facilities found for "${query}".` : "No facilities in this category."}
                  </p>
                </div>
              );
            if (facilityTab === "materials") {
              const storehouses = shown.filter(f => MATERIAL_STOREHOUSE_IDS.has(f.id));
              const production = shown.filter(f => !MATERIAL_STOREHOUSE_IDS.has(f.id));
              return (
                <div className="space-y-6">
                  <MaterialFacilitySection
                    title="Storehouse"
                    facilities={storehouses}
                    timeDiscount={timeDiscount}
                    resourceDiscount={resourceDiscount}
                  />
                  <MaterialFacilitySection
                    title="Production"
                    facilities={production}
                    timeDiscount={timeDiscount}
                    resourceDiscount={resourceDiscount}
                  />
                </div>
              );
            }
            return (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {shown.map(f => f.id === 17
                  ? <TownHallCard key={f.id} f={f} timeDiscount={timeDiscount} resourceDiscount={resourceDiscount} />
                  : <FacilityCard key={f.id} f={f} timeDiscount={timeDiscount} resourceDiscount={resourceDiscount} />
                )}
              </div>
            );
          })()}
        </div>
      ) : filtered.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {(() => {
            const seen = new Map<string, Building[]>();
            for (const b of filtered) {
              const k = slotKey(b);
              if (!seen.has(k)) seen.set(k, []);
              seen.get(k)!.push(b);
            }
            return Array.from(seen.values()).map(group => (
              <BuildingGroupCard key={slotKey(group[0])} buildings={group} />
            ));
          })()}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground py-10 text-center">
          No buildings found{q ? ` for "${query}"` : ""}.
        </p>
      )}
    </div>
  );
}

export { FacilityCard };


