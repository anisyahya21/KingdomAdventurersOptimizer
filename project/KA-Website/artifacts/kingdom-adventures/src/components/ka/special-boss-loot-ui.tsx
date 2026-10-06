import type { EncounterLoot } from "@/lib/special-boss-loot";
import { TREASURE_MONSTERS, TREASURE_SPECIAL_BOSSES } from "@/lib/treasure-lookup";
import { getMonsterSpriteById } from "@/lib/monster-sprites";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SlidersHorizontal } from "lucide-react";
import { useId, useState } from "react";

export const LOOT_DIFFICULTIES: EncounterLoot["difficulty"][] = ["Easy", "Normal", "Hard", "Extreme"];

export function canonicalLootBoss(encounter: EncounterLoot) {
  const difficulty = LOOT_DIFFICULTIES.indexOf(encounter.difficulty);
  const boss = TREASURE_SPECIAL_BOSSES.find((entry) =>
    entry.difficulty === difficulty && entry.level === encounter.level && entry.bossLevel === encounter.bossLevel,
  );
  const monster = boss && TREASURE_MONSTERS.find((entry) => entry.id === boss.boss);
  return monster ? { name: monster.name, id: monster.id } : undefined;
}

export function LootBossTag({ encounter }: { encounter: EncounterLoot }) {
  const boss = canonicalLootBoss(encounter);
  if (!boss) return null;
  const sprite = getMonsterSpriteById(boss.id);
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border/70 bg-background/50 px-1.5 py-0.5 text-xs font-normal text-muted-foreground">
      {sprite && <img src={sprite} alt="" className="h-6 w-6 object-contain" style={{ imageRendering: "pixelated" }} />}
      {boss.name}
    </span>
  );
}

export function LootBossIcon({ encounter, size = 60 }: { encounter: EncounterLoot; size?: number }) {
  const boss = canonicalLootBoss(encounter);
  if (!boss) return null;
  const sprite = getMonsterSpriteById(boss.id);
  if (!sprite) return null;
  return (
    <img
      src={sprite}
      alt={`${boss.name} boss`}
      className="shrink-0 object-contain"
      style={{ width: size, height: size, imageRendering: "pixelated" }}
    />
  );
}

export function lootDifficultyClasses(difficulty: EncounterLoot["difficulty"]) {
  switch (difficulty) {
    case "Easy": return "border-green-500/25 bg-green-500/[0.07]";
    case "Normal": return "border-yellow-500/25 bg-yellow-500/[0.07]";
    case "Hard": return "border-orange-500/25 bg-orange-500/[0.07]";
    case "Extreme": return "border-red-500/25 bg-red-500/[0.07]";
  }
}

export function LootDifficultySwitches({
  selected,
  onChange,
}: {
  selected: Set<EncounterLoot["difficulty"]>;
  onChange: (difficulty: EncounterLoot["difficulty"], checked: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const idPrefix = useId();

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" size="sm" aria-label="Choose visible difficulties">
          <SlidersHorizontal className="mr-1.5 h-4 w-4" /> Difficulties
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-44 p-3"
      >
        <div className="mb-2 text-sm font-semibold">Show difficulties</div>
        <div className="space-y-1">
          {LOOT_DIFFICULTIES.map((difficulty) => {
            const id = `${idPrefix}-loot-difficulty-${difficulty.toLowerCase()}`;
            return (
              <div key={difficulty} className="flex items-center gap-2 rounded-sm px-1 py-1.5">
                <Checkbox
                  id={id}
                  checked={selected.has(difficulty)}
                  onCheckedChange={(checked) => onChange(difficulty, checked === true)}
                />
                <Label htmlFor={id} className="flex-1 cursor-pointer text-sm font-normal">
                  {difficulty}
                </Label>
              </div>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export type LootDayFilterValue = string;

export function LootDayFilter({ value, onChange, days = [] }: { value: LootDayFilterValue; onChange: (value: LootDayFilterValue) => void; days?: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="Day filter">
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Day filter</span>
      {(["all", "today"] as const).map((filter) => (
        <button key={filter} type="button" onClick={() => onChange(filter)} aria-pressed={value === filter}
          className={`min-h-11 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${value === filter ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted"}`}>
          {filter === "all" ? "All days" : "Today"}
        </button>
      ))}
      {days.map((day) => (
        <button key={day} type="button" onClick={() => onChange(day)} aria-pressed={value === day}
          className={`min-h-11 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${value === day ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted"}`}>
          {/^[0-9]+$/.test(day) ? `Day ${day}` : day}
        </button>
      ))}
    </div>
  );
}

export function DifficultyDayLinks({
  day,
  onSelect,
}: {
  day: string;
  onSelect: (day: string, difficulty: EncounterLoot["difficulty"]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5" aria-label={`${day} loot difficulty links`}>
      {LOOT_DIFFICULTIES.map((difficulty) => (
        <button
          key={difficulty}
          type="button"
          onClick={(event) => { event.stopPropagation(); onSelect(day, difficulty); }}
          className={`min-h-11 rounded-md border px-3 py-1 text-[11px] font-medium transition-colors hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${lootDifficultyClasses(difficulty)}`}
        >
          {difficulty}
        </button>
      ))}
    </div>
  );
}
