import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Database, Search, Scale, TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SearchableSelect } from "@/components/searchable-select";
import { parseCsv } from "@/lib/monster-truth";
import monsterCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Monster.csv?raw";

type StatKey = "HP" | "MP" | "ATK" | "DEF" | "SPEED" | "LUCK" | "DEX";
type StatLevels = {
  level1: number;
  level100: number;
  level1000: number;
  level10000: number;
};
type MonsterStatRecord = {
  id: number;
  name: string;
  eggId: number | null;
  hatchCategory: number | null;
  allyExpRate: number | null;
  stats: Record<StatKey, StatLevels>;
};
type StatLevelKey = keyof StatLevels;
type StatLevelColumn = { key: StatLevelKey; label: string } | { key: "custom"; label: string; level: number };
type SortField = "name" | "id" | StatKey;

const STAT_KEYS: StatKey[] = ["HP", "MP", "ATK", "DEF", "SPEED", "LUCK", "DEX"];
const STAT_LEVEL_KEYS: StatLevelKey[] = ["level1", "level100", "level1000", "level10000"];
const STAT_LABELS: Record<StatKey, string> = {
  HP: "HP",
  MP: "MP",
  ATK: "Attack",
  DEF: "Def",
  SPEED: "Speed",
  LUCK: "Luck",
  DEX: "Dex",
};
const STAT_LEVEL_LABELS: Record<StatLevelKey, string> = {
  level1: "Lv 1",
  level100: "Lv 100",
  level1000: "Lv 1k",
  level10000: "Lv 10k",
};

function statValueAtLevel(stat: StatLevels, level: number) {
  const checkpoints = [
    { level: 1, value: stat.level1 },
    { level: 100, value: stat.level100 },
    { level: 1000, value: stat.level1000 },
    { level: 10000, value: stat.level10000 },
  ];
  const target = Math.max(1, Math.round(level));

  if (target <= checkpoints[0].level) return checkpoints[0].value;

  for (let index = 1; index < checkpoints.length; index += 1) {
    const left = checkpoints[index - 1];
    const right = checkpoints[index];
    if (target <= right.level) {
      const progress = (target - left.level) / (right.level - left.level);
      return Math.round(left.value + (right.value - left.value) * progress);
    }
  }

  const left = checkpoints[checkpoints.length - 2];
  const right = checkpoints[checkpoints.length - 1];
  const progress = (target - left.level) / (right.level - left.level);
  return Math.round(left.value + (right.value - left.value) * progress);
}

function valueForStatColumn(stat: StatLevels, column: StatLevelColumn) {
  return column.key === "custom" ? statValueAtLevel(stat, column.level) : stat[column.key];
}

function compactLevelLabel(level: number) {
  return level >= 1000 && level % 1000 === 0 ? `${level / 1000}k` : level.toLocaleString();
}

function parseNumber(value: string | undefined): number {
  const parsed = Number(value ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseMaybeNumber(value: string | undefined): number | null {
  const parsed = Number(value ?? "");
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeHeaderCell(value: string | undefined): string {
  return (value ?? "").trim().toUpperCase();
}

function buildMonsterStats(rawCsv: string): MonsterStatRecord[] {
  const rows = parseCsv(rawCsv).map((row) => row.map((cell) => cell.trim()));
  if (rows.length < 2) return [];

  const dataHeaderIndex = rows.findIndex((row) => {
    return normalizeHeaderCell(row[0]) === "ID" && normalizeHeaderCell(row[1]) === "NAME";
  });
  if (dataHeaderIndex < 0) return [];

  const dataHeader = rows[dataHeaderIndex];
  const statHeader = dataHeaderIndex > 0 ? rows[dataHeaderIndex - 1] : [];
  const statStarts = new Map<StatKey, number>();

  for (let index = 0; index < dataHeader.length; index += 1) {
    const statLabel = normalizeHeaderCell(statHeader[index]);
    const dataLabel = normalizeHeaderCell(dataHeader[index]);
    for (const key of STAT_KEYS) {
      if (statLabel === key || dataLabel.startsWith(key)) {
        if (!statStarts.has(key)) statStarts.set(key, index);
      }
    }
  }

  const eggIdIndex = dataHeader.indexOf("eggId");
  const hatchCategoryIndex = dataHeader.indexOf("hatchCategory");
  const allyExpRateIndex = dataHeader.indexOf("allyExpRate");

  return rows.slice(dataHeaderIndex + 1).map((cols) => {
    const stats = {} as Record<StatKey, StatLevels>;

    STAT_KEYS.forEach((key) => {
      const start = statStarts.get(key);
      if (start == null) {
        stats[key] = { level1: 0, level100: 0, level1000: 0, level10000: 0 };
        return;
      }
      stats[key] = {
        level1: parseNumber(cols[start]),
        level100: parseNumber(cols[start + 1]),
        level1000: parseNumber(cols[start + 2]),
        level10000: parseNumber(cols[start + 3]),
      };
    });

    return {
      id: parseNumber(cols[0]),
      name: cols[1] ?? `Monster ${cols[0] ?? ""}`,
      eggId: eggIdIndex >= 0 ? parseMaybeNumber(cols[eggIdIndex]) : null,
      hatchCategory: hatchCategoryIndex >= 0 ? parseMaybeNumber(cols[hatchCategoryIndex]) : null,
      allyExpRate: allyExpRateIndex >= 0 ? parseMaybeNumber(cols[allyExpRateIndex]) : null,
      stats,
    };
  }).filter((monster) => monster.name.trim().length > 0);
}

const MONSTERS = buildMonsterStats(monsterCsv);

export default function MonsterPetStatsPage() {
  const [query, setQuery] = useState("");
  const [sortField, setSortField] = useState<SortField>("name");
  const [sortLevel, setSortLevel] = useState<StatLevelKey>("level1");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [customLevel, setCustomLevel] = useState(500);

  const customLevelValue = Math.max(1, Math.round(customLevel || 1));
  const customLevelLabel = `Lv @${compactLevelLabel(customLevelValue)}`;
  const statColumns = useMemo<StatLevelColumn[]>(() => [
    ...STAT_LEVEL_KEYS.map((level) => ({ key: level, label: STAT_LEVEL_LABELS[level] }) as StatLevelColumn),
    { key: "custom", label: customLevelLabel, level: customLevelValue },
  ], [customLevelLabel, customLevelValue]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const searched = !q ? MONSTERS : MONSTERS.filter((monster) => {
      return monster.name.toLowerCase().includes(q) || String(monster.id).includes(q);
    });

    return [...searched].sort((left, right) => {
      let comparison = 0;
      if (sortField === "name") {
        comparison = left.name.localeCompare(right.name);
      } else if (sortField === "id") {
        comparison = left.id - right.id;
      } else {
        comparison = left.stats[sortField][sortLevel] - right.stats[sortField][sortLevel];
      }

      if (comparison === 0) comparison = left.name.localeCompare(right.name);
      return sortDirection === "asc" ? comparison : -comparison;
    });
  }, [query, sortDirection, sortField, sortLevel]);

  const compareMonsters = useMemo(() => {
    return compareIds
      .map((id) => MONSTERS.find((monster) => monster.id === id))
      .filter((monster): monster is MonsterStatRecord => !!monster);
  }, [compareIds]);

  const compareOptions = useMemo(() => {
    return MONSTERS.map((monster) => ({
      value: String(monster.id),
      label: `${monster.name} (#${monster.id})`,
    }));
  }, []);

  const addCompareMonster = (value: string) => {
    const id = Number(value);
    if (!Number.isFinite(id)) return;
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev;
      if (prev.length >= 4) return [...prev.slice(1), id];
      return [...prev, id];
    });
  };

  const removeCompareMonster = (id: number) => {
    setCompareIds((prev) => prev.filter((entry) => entry !== id));
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Database className="w-5 h-5 text-muted-foreground" />
          <h1 className="text-xl font-bold tracking-tight">Monster & Pet Stats</h1>
        </div>
        <div className="max-w-4xl space-y-2 text-sm leading-relaxed text-muted-foreground">
          <p>
            Detailed Kingdom Adventurers monster and pet stat database built from the mined <code>Monster.csv</code>.
            Search by monster name or id, sort by core stats, and compare up to four monsters side by side.
          </p>
          <p>
            Each stat is stored as a level-based block:
            <span className="font-medium text-foreground"> level 1, level 100, level 1,000, and level 10,000</span>,
            with a custom level column for quick pet and monster planning.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">How This Sheet Reads</CardTitle>
          <CardDescription>
            The mined monster sheet packs stats in blocks. This view focuses on HP, MP, Attack, Def, Speed, Luck, and Dex.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2 text-xs">
          {STAT_KEYS.map((key) => (
            <Badge key={key} variant="outline" className="font-medium">
              {STAT_LABELS[key]}: Lv 1 / 100 / 1k / 10k
            </Badge>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Search, Sort, and Compare</CardTitle>
          <CardDescription>Search by monster name or id, sort by a stat, and compare up to 4 monsters side by side.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_180px_160px_140px_auto]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search monster name or id..."
                className="pl-9"
              />
            </div>
            <SearchableSelect
              value={sortField}
              onChange={(value) => setSortField((value || "name") as SortField)}
              options={[
                { value: "name", label: "Sort: Name" },
                { value: "id", label: "Sort: ID" },
                ...STAT_KEYS.map((key) => ({ value: key, label: `Sort: ${STAT_LABELS[key]}` })),
              ]}
              triggerClassName="h-10"
            />
            <SearchableSelect
              value={sortLevel}
              onChange={(value) => setSortLevel((value || "level1") as StatLevelKey)}
              options={STAT_LEVEL_KEYS.map((level) => ({ value: level, label: `Use ${STAT_LEVEL_LABELS[level]}` }))}
              triggerClassName="h-10"
            />
            <label className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm font-medium text-muted-foreground">@</span>
              <Input
                type="number"
                inputMode="numeric"
                min={1}
                value={String(customLevel)}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  setCustomLevel(Number.isFinite(next) ? Math.max(1, Math.round(next)) : 1);
                }}
                aria-label="Custom monster stat level"
                className="h-10 pl-7"
              />
            </label>
            <Button
              variant="outline"
              className="gap-2"
              onClick={() => setSortDirection((prev) => prev === "asc" ? "desc" : "asc")}
            >
              {sortDirection === "asc" ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
              {sortDirection === "asc" ? "Ascending" : "Descending"}
            </Button>
          </div>

          <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-4">
            <div className="flex items-center gap-2">
              <Scale className="w-4 h-4 text-muted-foreground" />
              <div className="text-sm font-medium">Compare Monsters</div>
            </div>
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
              <SearchableSelect
                value=""
                clearOnSelect
                onChange={addCompareMonster}
                options={compareOptions}
                placeholder="Add monster to compare..."
                triggerClassName="h-10"
              />
              <Button variant="outline" onClick={() => setCompareIds([])} disabled={compareIds.length === 0}>
                Clear compare
              </Button>
            </div>
            {compareMonsters.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {compareMonsters.map((monster) => (
                  <Badge key={`${monster.id}-${monster.name}`} variant="outline" className="gap-2 py-1">
                    {monster.name} #{monster.id}
                    <button
                      type="button"
                      onClick={() => removeCompareMonster(monster.id)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      Ã—
                    </button>
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {compareMonsters.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Compare</CardTitle>
            <CardDescription>Side-by-side stat comparison using the same mined values.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead>
                  <tr className="border-b border-border text-muted-foreground">
                    <th className="text-left py-2 pr-3 font-medium">Stat</th>
                    {compareMonsters.map((monster) => (
                      <th key={`${monster.id}-${monster.name}`} className="text-left py-2 px-3 font-medium">
                        <div className="font-semibold text-foreground">{monster.name}</div>
                        <div className="text-xs text-muted-foreground">#{monster.id}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {STAT_KEYS.flatMap((key) => {
                    return statColumns.map((column, index) => (
                      (() => {
                        const values = compareMonsters.map((monster) => valueForStatColumn(monster.stats[key], column));
                        const maxValue = Math.max(...values);
                        const minValue = Math.min(...values);
                        const hasSpread = maxValue !== minValue;

                        return (
                      <tr key={`${key}-${column.key}`} className="border-b border-border/60 last:border-b-0">
                        <td className="py-2 pr-3">
                          <div className={index === 0 ? "font-medium text-foreground" : "text-muted-foreground"}>
                            {index === 0 ? STAT_LABELS[key] : column.label}
                          </div>
                        </td>
                        {compareMonsters.map((monster) => (
                          <td key={`${monster.id}-${key}-${column.key}`} className="py-2 px-3 tabular-nums">
                            {(() => {
                              const value = valueForStatColumn(monster.stats[key], column);
                              const isWinner = hasSpread && value === maxValue;
                              const isLoser = hasSpread && value === minValue;
                              const deltaFromBest = maxValue - value;
                              const deltaSpread = maxValue - minValue;

                              return (
                            <div className="flex items-center gap-1.5">
                              {isWinner && (
                                <TrendingUp className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                              )}
                              {isLoser && (
                                <TrendingDown className="w-3.5 h-3.5 text-red-500 shrink-0" />
                              )}
                              <span
                                className={
                                  isWinner
                                    ? "text-emerald-600 dark:text-emerald-400 font-semibold"
                                    : isLoser
                                    ? "text-red-600 dark:text-red-400 font-semibold"
                                    : ""
                                }
                              >
                                {value.toLocaleString()}
                              </span>
                              {hasSpread && (
                                <span
                                  className={
                                    isWinner
                                      ? "text-[11px] text-emerald-600 dark:text-emerald-400"
                                      : isLoser
                                      ? "text-[11px] text-red-600 dark:text-red-400"
                                      : "text-[11px] text-muted-foreground"
                                  }
                                >
                                  {isWinner ? `+${deltaSpread.toLocaleString()}` : `-${deltaFromBest.toLocaleString()}`}
                                </span>
                              )}
                            </div>
                              );
                            })()}
                          </td>
                        ))}
                      </tr>
                        );
                      })()
                    ));
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="text-sm text-muted-foreground">
        Showing <span className="font-medium text-foreground">{filtered.length}</span> of{" "}
        <span className="font-medium text-foreground">{MONSTERS.length}</span> mined monsters/pets.
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {filtered.map((monster) => (
          <Card key={`${monster.id}-${monster.name}`} className="overflow-hidden">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <CardTitle className="text-base leading-tight">{monster.name}</CardTitle>
                  <CardDescription className="flex flex-wrap gap-2 text-xs">
                    <span>ID {monster.id}</span>
                    {monster.eggId != null && <span>Egg {monster.eggId}</span>}
                    {monster.hatchCategory != null && <span>Hatch {monster.hatchCategory}</span>}
                    {monster.allyExpRate != null && <span>Ally EXP {monster.allyExpRate}</span>}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="text-left py-2 pr-3 font-medium">Stat</th>
                      {statColumns.map((column, index) => (
                        <th key={column.key} className={`text-right py-2 font-medium ${index === statColumns.length - 1 ? "pl-2" : "px-2"}`}>
                          {column.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {STAT_KEYS.map((key) => {
                      const stat = monster.stats[key];
                      return (
                        <tr key={key} className="border-b border-border/60 last:border-b-0">
                          <td className="py-2 pr-3 font-medium text-foreground">{STAT_LABELS[key]}</td>
                          {statColumns.map((column, index) => (
                            <td key={`${key}-${column.key}`} className={`py-2 text-right tabular-nums ${index === statColumns.length - 1 ? "pl-2" : "px-2"}`}>
                              {valueForStatColumn(stat, column).toLocaleString()}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

