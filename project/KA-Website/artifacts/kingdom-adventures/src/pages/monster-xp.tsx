import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Calculator, ChevronDown, ChevronUp, Info, Minus, Skull } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ALL_AREA_LEVELS, XP_TERRAINS, XP_UP_BONUSES, getXpResult, nativeTerrainAtLevel, type XpTerrain } from "@/lib/monster-xp";
import { getSkillIcon } from "@/lib/skill-icons";
import { localSharedData } from "@/lib/local-shared-data";

const terrainLabels: Record<XpTerrain, string> = {
  "Ground/dirt": "Ground / dug dirt",
  Grass: "Grass",
  Sand: "Sand",
  Rock: "Rock",
  Snow: "Snow",
  Swamp: "Swamp",
  Volcano: "Volcano",
};

const formatXp = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 1 });

const statDisplays: Array<{ name: "HP" | "MP" | "Vigor" | "Atk" | "Def" | "Spd" | "Luck"; iconKey: string }> = [
  { name: "HP", iconKey: "HP" },
  { name: "MP", iconKey: "MP" },
  { name: "Vigor", iconKey: "Vigor" },
  { name: "Atk", iconKey: "Attack" },
  { name: "Def", iconKey: "Defence" },
  { name: "Spd", iconKey: "Speed" },
  { name: "Luck", iconKey: "Luck" },
];

const statIcons = (localSharedData as { statIcons?: Record<string, string> }).statIcons ?? {};

function ComparisonArrow({ value, comparison }: { value: number; comparison?: number }) {
  if (comparison == null) return null;
  if (value === comparison) return <Minus className="h-3.5 w-3.5 text-muted-foreground" aria-label="Equal" />;
  if (value > comparison) return <ArrowUp className="h-4 w-4 text-emerald-500" aria-label="Higher than comparison" />;
  return <ArrowDown className="h-4 w-4 text-red-500" aria-label="Lower than comparison" />;
}

function ResultCard({ result, comparison }: { result: ReturnType<typeof getXpResult>; comparison?: ReturnType<typeof getXpResult> }) {
  const [open, setOpen] = useState(false);
  return (
    <Card className="border-border/70 bg-card/80">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">{terrainLabels[result.terrain]}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">Level {result.level} · {result.monsters.length} combat monsters in the pool</p>
          </div>
          <div className="text-right">
            <div className="flex items-center justify-end gap-1 text-2xl font-bold text-violet-500"><ComparisonArrow value={result.averageXp} comparison={comparison?.averageXp} />{formatXp(result.averageXp)}</div>
            <div className="text-[11px] text-muted-foreground">average XP / kill</div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="rounded-md bg-muted/40 p-2"><span className="text-muted-foreground">Range</span><div className="font-medium">{formatXp(result.minXp)} - {formatXp(result.maxXp)}</div></div>
          <div className="rounded-md bg-muted/40 p-2"><span className="text-muted-foreground">Avg. stat multiplier</span><div className="font-medium">{result.averageMultiplier.toFixed(3)}</div></div>
        </div>
        <div className="rounded-md bg-muted/40 p-2 text-xs">
          <div className="mb-2 font-medium">Average XP by stat per kill</div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 sm:grid-cols-4">
            {statDisplays.map(({ name: stat, iconKey }) => <div key={stat} className="flex items-center justify-start gap-3"><span className="flex min-w-[4.5rem] items-center gap-1.5 text-muted-foreground">{statIcons[iconKey] && <img src={statIcons[iconKey]} alt="" className="h-4 w-4 shrink-0 object-contain" />}{stat}</span><span className="flex items-center gap-1 font-medium"><ComparisonArrow value={result.statXp[stat]} comparison={comparison?.statXp[stat]} />{formatXp(result.statXp[stat])}</span></div>)}
          </div>
        </div>
        <Button variant="ghost" size="sm" className="h-7 w-full justify-between px-2 text-xs" onClick={() => setOpen((value) => !value)}>
          <span>Show eligible monsters</span>{open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </Button>
        {open && <div className="grid grid-cols-1 gap-1 border-t border-border/60 pt-2 text-xs sm:grid-cols-2">{result.monsters.map((monster) => <div key={monster.id} className="flex justify-between gap-2"><span>{monster.name}</span><span className="text-muted-foreground">{formatXp(30 * monster.averageMultiplier * result.level / 100 * result.bonusMultiplier)}</span></div>)}</div>}
      </CardContent>
    </Card>
  );
}

function terrainComparison(result: ReturnType<typeof getXpResult>, sameLevelResults: ReturnType<typeof getXpResult>[]) {
  if (sameLevelResults.length < 2) return undefined;
  if (sameLevelResults.length === 2) return sameLevelResults.find((other) => other.terrain !== result.terrain);
  const ground = sameLevelResults.find((other) => other.terrain === "Ground/dirt");
  return result.terrain === "Ground/dirt" ? undefined : ground;
}

function AreaLevelSelect({ label, value, onChange, inputId }: { label: string; value: number | ""; onChange: (value: number | "") => void; inputId: string }) {
  const [query, setQuery] = useState(value === "" ? "" : String(value));
  const filteredLevels = ALL_AREA_LEVELS.filter((areaLevel) => String(areaLevel).includes(query.trim()));

  return (
    <div>
      <label htmlFor={inputId} className="text-sm font-medium">{label}</label>
      <input
        id={inputId}
        list={`${inputId}-options`}
        inputMode="numeric"
        value={query}
        placeholder="Type to filter area levels"
        onChange={(event) => {
          const next = event.target.value;
          setQuery(next);
          if (next.trim() === "") {
            onChange("");
            return;
          }
          const parsed = Number(next);
          if (ALL_AREA_LEVELS.includes(parsed)) onChange(parsed);
        }}
        onBlur={() => {
          const parsed = Number(query);
          if (query.trim() !== "" && !ALL_AREA_LEVELS.includes(parsed)) setQuery(value === "" ? "" : String(value));
        }}
        className="mt-1 flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground"
      />
      <datalist id={`${inputId}-options`}>
        {filteredLevels.map((areaLevel) => <option key={areaLevel} value={areaLevel} />)}
      </datalist>
    </div>
  );
}

export default function MonsterXpPage() {
  const [levelA, setLevelA] = useState<number | "">("");
  const [levelB, setLevelB] = useState<number | "">("");
  const [compareEnabled, setCompareEnabled] = useState(false);
  const [enabledXpUps, setEnabledXpUps] = useState<number[]>([]);
  const [showAll, setShowAll] = useState(false);
  const resultsA = useMemo(() => levelA === "" ? [] : nativeTerrainAtLevel(levelA).map((terrain) => getXpResult(terrain, levelA, enabledXpUps)), [levelA, enabledXpUps]);
  const resultsB = useMemo(() => levelB === "" ? [] : nativeTerrainAtLevel(levelB).map((terrain) => getXpResult(terrain, levelB, enabledXpUps)), [levelB, enabledXpUps]);
  const allRows = useMemo(() => ALL_AREA_LEVELS.flatMap((areaLevel) => nativeTerrainAtLevel(areaLevel).map((terrain) => ({ areaLevel, result: getXpResult(terrain, areaLevel, enabledXpUps) }))), [enabledXpUps]);

  const toggleXpUp = (skill: number) => setEnabledXpUps((current) => current.includes(skill) ? current.filter((value) => value !== skill) : [...current, skill].sort());

  return (
    <div className="min-h-screen bg-background transition-colors">
      <div className="mx-auto max-w-7xl px-4 py-6">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-bold text-foreground"><Calculator className="h-5 w-5 text-violet-500" />Monster XP per kill</h1>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Expected battle XP by area level and terrain. The game chooses one non-zero exp stat at random, so each monster uses the average of its non-zero exp values.</p>
          </div>
          <Skull className="hidden h-8 w-8 text-muted-foreground/30 sm:block" />
        </div>

        <Card className="mb-6 border-violet-500/20 bg-violet-500/5">
          <CardContent className="grid gap-5 p-4 md:grid-cols-[180px_180px_1fr] md:items-end">
            <AreaLevelSelect label="Area level 1" inputId="area-level-1" value={levelA} onChange={setLevelA} />
            {compareEnabled ? <AreaLevelSelect label="Area level 2" inputId="area-level-2" value={levelB} onChange={setLevelB} /> : <div className="flex items-end"><Button type="button" variant="outline" className="h-10 w-full" onClick={() => setCompareEnabled(true)}>Compare another area</Button></div>}
            <div>
              <div className="mb-2 text-sm font-medium">XP Up skills</div>
              <div className="flex flex-wrap gap-2">{([1, 2, 3] as const).map((skill) => { const roman = skill === 1 ? "Ⅰ" : skill === 2 ? "Ⅱ" : "Ⅲ"; const skillName = `Experience UP ${roman}`; const icon = getSkillIcon(skillName); return <Button key={skill} type="button" variant={enabledXpUps.includes(skill) ? "default" : "outline"} size="sm" onClick={() => toggleXpUp(skill)}>{icon && <img src={icon} alt="" className="h-5 w-5 rounded-sm object-contain" />}{skillName} <span className="ml-1 opacity-70">×{XP_UP_BONUSES[skill].toFixed(2)}</span></Button>; })}</div>
              <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground"><Info className="h-3.5 w-3.5" />XP Up bonuses multiply together. Active multiplier: ×{enabledXpUps.reduce((product, skill) => product * XP_UP_BONUSES[skill as 1 | 2 | 3], 1).toFixed(2)}</p>
            </div>
          </CardContent>
        </Card>

        <div className="mb-3 flex items-end justify-between gap-3"><div><h2 className="text-lg font-semibold">{levelA === "" ? "Choose an area level" : compareEnabled && levelB !== "" ? "Area level comparison" : "Area level results"}</h2><p className="text-xs text-muted-foreground">Ground/dirt represents digging. Other rows appear when that biome exists at the selected level.</p>{levelA !== "" && (resultsA.length > 1 || resultsB.length > 1) && <p className="mt-1 text-xs text-muted-foreground">Arrows compare terrain values within each selected area level.</p>}</div>{compareEnabled && <Button type="button" variant="ghost" size="sm" onClick={() => { setCompareEnabled(false); setLevelB(""); }}>Remove comparison</Button>}</div>
        {levelA === "" ? <Card className="border-border/70 bg-card/80"><CardContent className="p-6 text-sm text-muted-foreground">Choose an available area level to see XP per kill.</CardContent></Card> : <div className="grid gap-4 md:grid-cols-2">{[...resultsA, ...(compareEnabled ? resultsB : [])].map((result) => <ResultCard key={`${result.level}-${result.terrain}`} result={result} comparison={terrainComparison(result, result.level === levelA ? resultsA : resultsB)} />)}</div>}

        <Card className="mt-6 border-border/70 bg-card/80">
          <CardHeader className="flex-row items-center justify-between space-y-0"><div><CardTitle className="text-base">All native area levels</CardTitle><p className="mt-1 text-xs text-muted-foreground">Combat-only pools. Type 1 entries from Monster.csv are excluded as farmable animals.</p></div><Button variant="outline" size="sm" onClick={() => setShowAll((value) => !value)}>{showAll ? "Hide table" : "Show table"}</Button></CardHeader>
          {showAll && <CardContent className="overflow-x-auto"><table className="w-full min-w-[620px] text-left text-sm"><thead><tr className="border-b border-border/60 text-xs text-muted-foreground"><th className="p-2">Area level</th><th className="p-2">Terrain</th><th className="p-2 text-right">Monsters</th><th className="p-2 text-right">Average XP / kill</th><th className="p-2 text-right">Range</th></tr></thead><tbody>{allRows.map(({ areaLevel, result }) => <tr key={`${areaLevel}-${result.terrain}`} className="border-b border-border/40"><td className="p-2 font-medium">{areaLevel}</td><td className="p-2">{terrainLabels[result.terrain]}</td><td className="p-2 text-right">{result.monsters.length}</td><td className="p-2 text-right font-medium text-violet-500">{formatXp(result.averageXp)}</td><td className="p-2 text-right text-muted-foreground">{formatXp(result.minXp)} - {formatXp(result.maxXp)}</td></tr>)}</tbody></table></CardContent>}
        </Card>
      </div>
    </div>
  );
}
