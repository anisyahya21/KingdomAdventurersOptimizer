import { useMemo, useState } from "react";
import { ArrowDownUp, Building2, FlaskConical, ImageOff, Package, Search } from "lucide-react";
import { PageHeader } from "@/components/ka/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useEquipmentIcons } from "@/hooks/use-equipment-icons";
import { getEquipmentIcon, getFacilityIconByName, getFurnitureIcon, getItemIcon } from "@/lib/equipment-icons";
import { localSharedData } from "@/lib/local-shared-data";
import { parseCsv } from "@/lib/monster-truth";
import researchCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Research.csv?raw";
import equipCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Equip.csv?raw";

type ResearchCategory = 0 | 1 | 2;

type RequirementItem = {
  name: string;
  qty: number | null;
};

type ResearchEntry = {
  id: number;
  name: string;
  category: ResearchCategory;
  equipmentRank: string | null;
  researchMax: number | null;
  maxLevel: number;
  townRankRaw: number | null;
  townRank: number | null;
  requirements: RequirementItem[];
};

type RequirementPeak = {
  name: string;
  qty: number;
};

type EquipmentProfile = {
  name: string;
  rank: string | null;
  statsAt99: Array<{ label: string; value: number }>;
};

const EQUIP_STAT_ORDER = ["HP", "MP", "Vigor", "Attack", "Defence", "Speed", "Luck", "Intelligence", "Dexterity", "Gather", "Move", "Heart"];

const EQUIP_LABEL_MAP: Record<string, string> = {
  HP: "HP",
  MP: "MP",
  Atk: "Attack",
  Def: "Defence",
  Spd: "Speed",
  Lck: "Luck",
  Int: "Intelligence",
  Dex: "Dexterity",
  Gth: "Gather",
  Mov: "Move",
  Hrt: "Heart",
  Vigor: "Vigor",
  Attack: "Attack",
  Defence: "Defence",
  Speed: "Speed",
  Luck: "Luck",
  Intelligence: "Intelligence",
  Dexterity: "Dexterity",
  Gather: "Gather",
  Move: "Move",
  Heart: "Heart",
};

function normalizeHeader(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function findColumnIndex(headers: string[], expectedName: string): number {
  const target = normalizeHeader(expectedName);
  return headers.findIndex((header) => normalizeHeader(header) === target);
}

function parseIntSafe(value: string | undefined): number | null {
  if (!value) return null;
  const parsed = Number.parseInt(value.trim(), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function getEquipmentRank(name: string): string | null {
  const trimmed = name.trim();
  const prefixMatch = trimmed.match(/^([SABCDEF])\//i);
  if (prefixMatch?.[1]) return prefixMatch[1].toUpperCase();
  const suffixMatch = trimmed.match(/\(([SABCDEF])\)$/i);
  if (suffixMatch?.[1]) return suffixMatch[1].toUpperCase();
  return null;
}

function statAtLevel(base: number, increment: number, level: number): number {
  return Math.round(base + (level - 1) * increment);
}

function parseEquipmentProfiles(): Map<string, EquipmentProfile> {
  const rows = parseCsv(equipCsv);
  const profiles = new Map<string, EquipmentProfile>();
  if (rows.length < 2) return profiles;

  const header = rows[0] ?? [];
  const nameIndex = header.findIndex((column) => String(column).trim().toLowerCase() === "name");
  if (nameIndex < 0) return profiles;

  const statColumns: Array<{ label: string; start: number; increment: number }> = [];
  for (let index = 0; index < header.length - 1; index += 1) {
    const current = String(header[index] ?? "").trim();
    const next = String(header[index + 1] ?? "").trim().toLowerCase();
    const startMatch = current.match(/^(.+?)\s+start$/i);
    if (!startMatch || next !== "increment") continue;
    const normalized = EQUIP_LABEL_MAP[startMatch[1].trim()];
    if (!normalized) continue;
    statColumns.push({ label: normalized, start: index, increment: index + 1 });
  }

  for (const row of rows.slice(1)) {
    const name = String(row[nameIndex] ?? "").trim();
    if (!name || !/^([SABCDEF])\s*\//i.test(name)) continue;
    if (profiles.has(name)) continue;

    const statsAt99 = statColumns
      .map((col) => {
        const base = Number(row[col.start]) || 0;
        const increment = Number(row[col.increment]) || 0;
        return {
          label: col.label,
          value: statAtLevel(base, increment, 99),
        };
      })
      .filter((stat) => stat.value > 0)
      .sort((a, b) => {
        const aIndex = EQUIP_STAT_ORDER.indexOf(a.label);
        const bIndex = EQUIP_STAT_ORDER.indexOf(b.label);
        return (aIndex === -1 ? 999 : aIndex) - (bIndex === -1 ? 999 : bIndex);
      });

    profiles.set(name, {
      name,
      rank: getEquipmentRank(name),
      statsAt99,
    });
  }

  return profiles;
}

function findEquipmentProfile(profiles: Map<string, EquipmentProfile>, equipmentName: string): EquipmentProfile | null {
  const exact = profiles.get(equipmentName);
  if (exact) return exact;

  const withoutSample = equipmentName.replace(/\s+sample$/i, "").trim();
  if (withoutSample !== equipmentName) {
    const sampleLess = profiles.get(withoutSample);
    if (sampleLess) return sampleLess;
  }

  return null;
}

function normalizeTownRank(rawValue: number | null): number | null {
  if (rawValue === null || rawValue <= 0) return null;
  if (rawValue === 999) return null;
  return rawValue;
}

function parseResearchEntries(): ResearchEntry[] {
  const rows = parseCsv(researchCsv);
  if (rows.length < 2) return [];

  const headers = rows[0].map((header, index) => (header?.trim() ? header.trim() : `H${index + 1}`));
  const idIndex = 0;
  const nameIndex = findColumnIndex(headers, "nameText");
  const categoryIndex = findColumnIndex(headers, "category");
  const maxLevelIndex = findColumnIndex(headers, "maxLevel");
  const researchMaxIndex = findColumnIndex(headers, "researchMax");
  const townRankIndex = findColumnIndex(headers, "findTermTextReleaseTownRank");
  const item1NameIndex = findColumnIndex(headers, "findTermTextItemName1");
  const item1QtyIndex = findColumnIndex(headers, "findTermTextItemNum1");
  const item2NameIndex = findColumnIndex(headers, "findTermTextItemName2");
  const item2QtyIndex = findColumnIndex(headers, "findTermTextItemNum2");

  return rows
    .slice(1)
    .map((row) => {
      const id = parseIntSafe(row[idIndex]) ?? -1;
      const name = (nameIndex >= 0 ? row[nameIndex] : "")?.trim() ?? "";
      const categoryNumber = parseIntSafe(categoryIndex >= 0 ? row[categoryIndex] : undefined) ?? 0;
      const category = (categoryNumber === 0 || categoryNumber === 1 || categoryNumber === 2
        ? categoryNumber
        : 0) as ResearchCategory;
      const maxLevel = parseIntSafe(maxLevelIndex >= 0 ? row[maxLevelIndex] : undefined) ?? 0;
      const researchMax = parseIntSafe(researchMaxIndex >= 0 ? row[researchMaxIndex] : undefined);
      const townRankRaw = parseIntSafe(townRankIndex >= 0 ? row[townRankIndex] : undefined);

      const requirementNames = [
        item1NameIndex >= 0 ? row[item1NameIndex] : "",
        item2NameIndex >= 0 ? row[item2NameIndex] : "",
      ];
      const requirementQtys = [
        item1QtyIndex >= 0 ? row[item1QtyIndex] : "",
        item2QtyIndex >= 0 ? row[item2QtyIndex] : "",
      ];

      const requirements: RequirementItem[] = requirementNames
        .map((rawName, index) => {
          const cleanName = (rawName ?? "").trim();
          if (!cleanName || cleanName === "-" || cleanName === "Unused" || cleanName === "\u672a\u4f7f\u7528") {
            return null;
          }
          return {
            name: cleanName,
            qty: parseIntSafe(requirementQtys[index]) ?? null,
          };
        })
        .filter((entry): entry is RequirementItem => Boolean(entry));

      return {
        id,
        name,
        category,
        equipmentRank: category === 1 ? getEquipmentRank(name) : null,
        researchMax,
        maxLevel,
        townRankRaw,
        townRank: normalizeTownRank(townRankRaw),
        requirements,
      };
    })
    .filter((entry) => entry.name.length > 0 && entry.id >= 0)
    .sort((a, b) => a.id - b.id);
}

function getResearchEntryIcon(entry: ResearchEntry, equipmentIcons: Record<string, string>) {
  if (entry.category === 1) {
    return getEquipmentIcon(equipmentIcons, entry.name);
  }
  if (entry.category === 2) {
    return getItemIcon(entry.name);
  }
  return getFacilityIconByName(entry.name) ?? getFurnitureIcon(entry.name) ?? getItemIcon(entry.name);
}

function getRequirementItemIcon(name: string, equipmentIcons: Record<string, string>) {
  return getItemIcon(name) ?? getEquipmentIcon(equipmentIcons, name);
}

function requirementScore(entry: ResearchEntry): number {
  const qtySum = entry.requirements.reduce((sum, requirement) => sum + (requirement.qty ?? 0), 0);
  const townRankWeight = (entry.townRank ?? 0) * 100;
  return townRankWeight + qtySum;
}

function maxLevelLabel(entry: ResearchEntry): string {
  if (entry.researchMax === -1) return "Unlimited";
  if (entry.maxLevel > 0) return String(entry.maxLevel);
  return "-";
}

function aggregateRequirementPeaks(entries: ResearchEntry[]): RequirementPeak[] {
  const peaks = new Map<string, number>();

  for (const entry of entries) {
    for (const requirement of entry.requirements) {
      const qty = requirement.qty ?? 0;
      if (qty <= 0) continue;
      peaks.set(requirement.name, Math.max(peaks.get(requirement.name) ?? 0, qty));
    }
  }

  return Array.from(peaks.entries())
    .map(([name, qty]) => ({ name, qty }))
    .sort((a, b) => b.qty - a.qty || a.name.localeCompare(b.name));
}

function RequirementsCell({
  entry,
  equipmentIcons,
}: {
  entry: ResearchEntry;
  equipmentIcons: Record<string, string>;
}) {
  return (
    <div className="space-y-1.5">
      {entry.requirements.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {entry.requirements.map((requirement) => {
            const iconPath = getRequirementItemIcon(requirement.name, equipmentIcons);
            return (
              <div key={`${entry.id}-${requirement.name}`} className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-muted/20 px-1.5 py-1 text-xs">
                {iconPath ? (
                  <img
                    src={iconPath}
                    alt={requirement.name}
                    className="h-4 w-4 object-contain"
                    style={{ imageRendering: "pixelated" }}
                  />
                ) : (
                  <Package className="h-4 w-4 text-muted-foreground" />
                )}
                <span className="max-w-[180px] truncate">{requirement.name}</span>
                <span className="font-semibold">x{requirement.qty ?? 0}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground">No item requirement listed</div>
      )}
    </div>
  );
}

function ResearchTable({
  entries,
  equipmentIcons,
  showEquipmentRankColumn = false,
  onSelectEquipment,
}: {
  entries: ResearchEntry[];
  equipmentIcons: Record<string, string>;
  showEquipmentRankColumn?: boolean;
  onSelectEquipment?: (equipmentName: string) => void;
}) {
  const [townRankSort, setTownRankSort] = useState<"none" | "asc" | "desc">("none");
  const [equipmentRankSort, setEquipmentRankSort] = useState<"none" | "asc" | "desc">("none");

  const rankScoreByLabel: Record<string, number> = {
    S: 7,
    A: 6,
    B: 5,
    C: 4,
    D: 3,
    E: 2,
    F: 1,
  };

  const rows = useMemo(() => {
    let output = entries;

    if (showEquipmentRankColumn && equipmentRankSort !== "none") {
      output = [...output].sort((a, b) => {
        const aScore = a.equipmentRank ? rankScoreByLabel[a.equipmentRank] ?? 0 : 0;
        const bScore = b.equipmentRank ? rankScoreByLabel[b.equipmentRank] ?? 0 : 0;
        const delta = equipmentRankSort === "asc" ? bScore - aScore : aScore - bScore;
        if (delta !== 0) return delta;
        return a.name.localeCompare(b.name);
      });
    }

    if (townRankSort !== "none") {
      output = [...output].sort((a, b) => {
        const aRank = a.townRank ?? -1;
        const bRank = b.townRank ?? -1;
        const delta = townRankSort === "asc" ? aRank - bRank : bRank - aRank;
        if (delta !== 0) return delta;
        return a.name.localeCompare(b.name);
      });
    }

    return output;
  }, [entries, showEquipmentRankColumn, equipmentRankSort, townRankSort]);

  function cycleTownRankSort() {
    setTownRankSort((current) => {
      if (current === "none") return "asc";
      if (current === "asc") return "desc";
      return "none";
    });
  }

  function cycleEquipmentRankSort() {
    setEquipmentRankSort((current) => {
      if (current === "none") return "asc";
      if (current === "asc") return "desc";
      return "none";
    });
  }

  function townRankLabel(entry: ResearchEntry): string {
    return entry.townRank === null ? "-" : String(entry.townRank);
  }

  if (entries.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-sm text-muted-foreground">No matching research entries.</CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[56px]">Icon</TableHead>
              <TableHead>Name</TableHead>
              {showEquipmentRankColumn ? (
                <TableHead className="w-[120px]">
                  <button
                    type="button"
                    onClick={cycleEquipmentRankSort}
                    className="inline-flex items-center gap-1 text-left hover:text-foreground"
                  >
                    Rank
                    <ArrowDownUp className="h-3.5 w-3.5" />
                  </button>
                </TableHead>
              ) : null}
              <TableHead className="w-[180px]">
                <button
                  type="button"
                  onClick={cycleTownRankSort}
                  className="inline-flex items-center gap-1 text-left hover:text-foreground"
                >
                  Town Hall Rank
                  <ArrowDownUp className="h-3.5 w-3.5" />
                </button>
              </TableHead>
              <TableHead className="w-[120px]">Max Level</TableHead>
              <TableHead>Unlock Requirements</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((entry) => {
              const iconPath = getResearchEntryIcon(entry, equipmentIcons);
              const canOpenEquipment = entry.category === 1 && Boolean(onSelectEquipment);
              return (
                <TableRow key={entry.id}>
                  <TableCell>
                    {canOpenEquipment ? (
                      <button
                        type="button"
                        onClick={() => onSelectEquipment?.(entry.name)}
                        className="rounded-sm hover:opacity-90"
                        title="Open level 99 stats"
                      >
                        {iconPath ? (
                          <img
                            src={iconPath}
                            alt={entry.name}
                            className="h-9 w-9 object-contain"
                            style={{ imageRendering: "pixelated" }}
                          />
                        ) : (
                          <ImageOff className="h-5 w-5 text-muted-foreground" />
                        )}
                      </button>
                    ) : iconPath ? (
                      <img
                        src={iconPath}
                        alt={entry.name}
                        className="h-9 w-9 object-contain"
                        style={{ imageRendering: "pixelated" }}
                      />
                    ) : (
                      <ImageOff className="h-5 w-5 text-muted-foreground" />
                    )}
                  </TableCell>
                  <TableCell>
                    {canOpenEquipment ? (
                      <button
                        type="button"
                        onClick={() => onSelectEquipment?.(entry.name)}
                        className="font-medium text-left hover:underline"
                        title="Open level 99 stats"
                      >
                        {entry.name}
                      </button>
                    ) : (
                      <div className="font-medium">{entry.name}</div>
                    )}
                  </TableCell>
                  {showEquipmentRankColumn ? (
                    <TableCell>
                      <span className="font-medium">{entry.equipmentRank ?? "-"}</span>
                    </TableCell>
                  ) : null}
                  <TableCell>
                    <span className="font-medium">{townRankLabel(entry)}</span>
                  </TableCell>
                  <TableCell>{maxLevelLabel(entry)}</TableCell>
                  <TableCell>
                    <RequirementsCell entry={entry} equipmentIcons={equipmentIcons} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function HoardSummary({
  peaks,
  equipmentIcons,
}: {
  peaks: RequirementPeak[];
  equipmentIcons: Record<string, string>;
}) {
  if (peaks.length === 0) return null;

  return (
    <Card>
      <CardContent className="p-3 space-y-2">
        <div className="text-sm font-medium">Peak Unlock Requirements (from Most Expensive list)</div>
        <div className="flex flex-wrap gap-2">
          {peaks.map((peak) => {
            const iconPath = getRequirementItemIcon(peak.name, equipmentIcons);
            return (
              <div key={peak.name} className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-muted/20 px-1.5 py-1 text-xs">
                {iconPath ? (
                  <img
                    src={iconPath}
                    alt={peak.name}
                    className="h-4 w-4 object-contain"
                    style={{ imageRendering: "pixelated" }}
                  />
                ) : (
                  <Package className="h-4 w-4 text-muted-foreground" />
                )}
                <span className="max-w-[180px] truncate">{peak.name}</span>
                <span className="font-semibold">x{peak.qty}</span>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

export default function ResearchPage() {
  const [search, setSearch] = useState("");
  const [selectedEquipmentName, setSelectedEquipmentName] = useState<string | null>(null);
  const equipmentIcons = useEquipmentIcons();
  const statIcons = useMemo(() => {
    const icons = (localSharedData as { statIcons?: Record<string, unknown> }).statIcons ?? {};
    const normalized: Record<string, string> = {};
    for (const [key, value] of Object.entries(icons)) {
      if (typeof value === "string" && value.length > 0) normalized[key] = value;
    }
    return normalized;
  }, []);

  const allEntries = useMemo(() => parseResearchEntries(), []);
  const equipmentProfiles = useMemo(() => parseEquipmentProfiles(), []);

  const filteredEntries = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return allEntries;
    return allEntries.filter((entry) => {
      if (entry.name.toLowerCase().includes(term)) return true;
      return entry.requirements.some((item) => item.name.toLowerCase().includes(term));
    });
  }, [allEntries, search]);

  const facilities = useMemo(() => filteredEntries.filter((entry) => entry.category === 0), [filteredEntries]);
  const equipment = useMemo(() => filteredEntries.filter((entry) => entry.category === 1), [filteredEntries]);
  const items = useMemo(() => filteredEntries.filter((entry) => entry.category === 2), [filteredEntries]);
  const mostExpensive = useMemo(() => {
    return [...filteredEntries]
      .sort((a, b) => requirementScore(b) - requirementScore(a) || (b.townRank ?? 0) - (a.townRank ?? 0) || b.id - a.id)
      .slice(0, 60);
  }, [filteredEntries]);
  const requirementPeaks = useMemo(() => aggregateRequirementPeaks(mostExpensive), [mostExpensive]);
  const selectedEquipmentProfile = selectedEquipmentName
    ? findEquipmentProfile(equipmentProfiles, selectedEquipmentName)
    : null;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
      <PageHeader icon={<FlaskConical className="w-5 h-5" />} title="Research Database">
        <p>
          Data source: KA GameData research tab. Category split: Facility, Equipment, and Items.
        </p>
        <p>
          Town Hall Rank comes from findTermTextReleaseTownRank. If no rank requirement exists, it is shown as -.
        </p>
      </PageHeader>

      <Card>
        <CardContent className="p-3">
          <div className="relative max-w-md">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by research name or requirement item"
              className="pl-8"
            />
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="all" className="space-y-3">
        <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto rounded-none border-b bg-transparent p-0">
          <TabsTrigger
            value="all"
            className="rounded-b-none rounded-t-md border border-b-0 bg-muted/40 px-3 py-2 data-[state=active]:bg-background data-[state=active]:shadow-none"
          >
            Research All ({filteredEntries.length})
          </TabsTrigger>
          <TabsTrigger
            value="facility"
            className="rounded-b-none rounded-t-md border border-b-0 bg-muted/40 px-3 py-2 data-[state=active]:bg-background data-[state=active]:shadow-none"
          >
            Reseach Facility ({facilities.length})
          </TabsTrigger>
          <TabsTrigger
            value="equipment"
            className="rounded-b-none rounded-t-md border border-b-0 bg-muted/40 px-3 py-2 data-[state=active]:bg-background data-[state=active]:shadow-none"
          >
            Research Equipment ({equipment.length})
          </TabsTrigger>
          <TabsTrigger
            value="items"
            className="rounded-b-none rounded-t-md border border-b-0 bg-muted/40 px-3 py-2 data-[state=active]:bg-background data-[state=active]:shadow-none"
          >
            Research Items ({items.length})
          </TabsTrigger>
          <TabsTrigger
            value="expensive"
            className="rounded-b-none rounded-t-md border border-b-0 bg-muted/40 px-3 py-2 data-[state=active]:bg-background data-[state=active]:shadow-none"
          >
            Most Expensive ({mostExpensive.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="all">
          <ResearchTable
            entries={filteredEntries}
            equipmentIcons={equipmentIcons}
            onSelectEquipment={setSelectedEquipmentName}
          />
        </TabsContent>

        <TabsContent value="facility">
          <ResearchTable entries={facilities} equipmentIcons={equipmentIcons} />
        </TabsContent>

        <TabsContent value="equipment">
          <ResearchTable
            entries={equipment}
            equipmentIcons={equipmentIcons}
            showEquipmentRankColumn
            onSelectEquipment={setSelectedEquipmentName}
          />
        </TabsContent>

        <TabsContent value="items">
          <ResearchTable entries={items} equipmentIcons={equipmentIcons} />
        </TabsContent>

        <TabsContent value="expensive" className="space-y-3">
          <HoardSummary peaks={requirementPeaks} equipmentIcons={equipmentIcons} />
          <ResearchTable entries={mostExpensive} equipmentIcons={equipmentIcons} />
        </TabsContent>
      </Tabs>

      <Card>
        <CardContent className="py-3 text-xs text-muted-foreground">
          <div className="flex items-start gap-2">
            <Building2 className="mt-0.5 h-4 w-4 shrink-0" />
            <p>
              Town Hall unlock values come from <span className="font-medium">findTermTextReleaseTownRank</span>. Entries without a Town Hall requirement are shown as -.
            </p>
          </div>
        </CardContent>
      </Card>

      <Dialog open={Boolean(selectedEquipmentName)} onOpenChange={(open) => !open && setSelectedEquipmentName(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {selectedEquipmentProfile?.name ?? selectedEquipmentName}
              {selectedEquipmentProfile?.rank ? ` (${selectedEquipmentProfile.rank})` : ""}
              {" - Level 99"}
            </DialogTitle>
          </DialogHeader>
          {selectedEquipmentProfile && selectedEquipmentProfile.statsAt99.length > 0 ? (
            <div className="grid grid-cols-2 gap-2 text-sm">
              {selectedEquipmentProfile.statsAt99.map((stat) => (
                <div key={stat.label} className="flex items-center justify-between rounded-md border border-border/70 bg-muted/20 px-2 py-1">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    {statIcons[stat.label] ? (
                      <img src={statIcons[stat.label]} alt={stat.label} className="h-4 w-4 object-contain" />
                    ) : null}
                    {stat.label}
                  </span>
                  <span className="font-semibold tabular-nums">{stat.value}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">No Level 99 stat data found for this equipment.</div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
