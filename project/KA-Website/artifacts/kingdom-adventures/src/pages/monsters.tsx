import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Info, Search, Skull } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ANIMAL_MONSTER_CARDS,
  COMBAT_MONSTER_CARDS,
  MAX_AREA_LEVEL,
  filterMonsterCards,
  selectableAreaLevels,
  statXp,
  xpPerKill,
  type MonsterCard,
} from "@/lib/monster-cards";

const formatNumber = (value: number, digits = 1) =>
  value.toLocaleString(undefined, { maximumFractionDigits: digits });

function terrainChipClass(card: MonsterCard) {
  return `border-border bg-muted/40 text-foreground/90 chip-terrain-${card.terrainClass}`;
}

function MonsterArt({ card }: { card: MonsterCard }) {
  return (
    <div className="flex h-[168px] items-center justify-center rounded-t-[9px] bg-[radial-gradient(115%_90%_at_50%_105%,hsl(258_32%_22%),hsl(222_28%_14%)_62%)]">
      {card.sprite ? (
        <img
          src={card.sprite}
          alt={card.name}
          className="h-[150px] w-auto [image-rendering:pixelated]"
          loading="lazy"
        />
      ) : (
        <Skull className="h-10 w-10 text-muted-foreground/40" />
      )}
    </div>
  );
}

function Chips({ card }: { card: MonsterCard }) {
  return (
    <div className="flex flex-wrap items-stretch gap-1.5 px-2.5 pt-1.5">
      <span
        className={`inline-flex items-center gap-1 self-center rounded border px-1.5 py-0.5 text-xs ${terrainChipClass(card)}`}
      >
        <span className="font-semibold">
          {card.terrainName} {card.minLevel}+
        </span>
      </span>
      {card.cave && (
        <span className="ml-auto flex flex-1 items-center gap-2 rounded-md border border-border bg-muted/30 px-2 py-1">
          <img
            src={card.cave.icon}
            alt=""
            className="h-10 w-10 shrink-0 object-contain [image-rendering:pixelated]"
          />
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground/90">{card.cave.name}</span>
          <span className="shrink-0 text-xs font-semibold tabular-nums text-muted-foreground">Lv {card.minLevel}+</span>
        </span>
      )}
    </div>
  );
}

function BoxBlock({ card, animal }: { card: MonsterCard; animal: boolean }) {
  if (!card.box) return null;
  return (
    <div className="mt-2 border-t border-border/60 px-2.5 pt-2">
      <div className="flex items-center gap-2">
        <img src={card.box.icon} alt="" className="h-9 w-9 object-contain [image-rendering:pixelated]" />
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold">{card.box.name}</span>
          <span className="block text-[11px] text-muted-foreground">
            {animal ? "Material yield" : `Treasure #${card.box.id}`}
          </span>
        </span>
        <span className="ml-auto text-right">
          <span className="block text-sm font-semibold text-violet-400">{card.box.rate}%</span>
          <span className="block text-[11px] text-muted-foreground">
            {animal ? "when collected" : "box per kill"}
          </span>
        </span>
      </div>
      <ul className="mt-1.5 space-y-[3px]">
        {card.box.rewards.map((reward) => (
          <li
            key={`${card.id}-${reward.name}`}
            className="flex items-center gap-2 rounded bg-muted/50 px-2 py-0"
          >
            {reward.icon ? (
              <img src={reward.icon} alt="" className="h-6 w-6 object-contain [image-rendering:pixelated]" />
            ) : (
              <span className="h-6 w-6" />
            )}
            <span className="min-w-0 truncate text-xs text-foreground/90">
              {reward.name} <span className="text-muted-foreground">{formatRange(reward.min, reward.max)}</span>
            </span>
            <span className="ml-auto text-xs font-semibold tabular-nums">{reward.rate}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function formatRange(min: number, max: number) {
  return min === max ? `×${min}` : `×${min}-${max}`;
}

function XpBlock({ card }: { card: MonsterCard }) {
  const levelSuggestions = useMemo(() => selectableAreaLevels(card.minLevel), [card.minLevel]);
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState(card.minLevel);
  const [draft, setDraft] = useState(String(card.minLevel));
  const statEntries = useMemo(() => statXp(card, level), [card, level]);
  const typed = draft.trim();
  const filteredLevels = typed ? levelSuggestions.filter((entry) => String(entry).startsWith(typed)) : levelSuggestions;
  const commit = (raw: string) => {
    const parsed = Number(raw);
    if (raw.trim() !== "" && Number.isInteger(parsed) && parsed >= card.minLevel && parsed <= MAX_AREA_LEVEL) {
      setLevel(parsed);
      setDraft(String(parsed));
      return;
    }
    // Nothing to commit yet (empty or below the lowest spawn level): keep the last valid level.
    if (raw.trim() === "") setDraft("");
  };
  return (
    <div className="mt-2 border-t border-border/60 bg-muted/20">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3 w-3 text-muted-foreground" /> : <ChevronRight className="h-3 w-3 text-muted-foreground" />}
        <span className="font-medium">XP per kill</span>
        <span className="ml-auto text-sm font-semibold tabular-nums text-violet-400">
          {formatNumber(xpPerKill(card, level))}
        </span>
        <span className="text-[11px] text-muted-foreground">
          at Lv {level}
        </span>
      </button>
      {open && (
        <div className="space-y-2 px-2.5 pb-2.5">
          <div className="flex items-center gap-2">
            <label htmlFor={`level-${card.id}`} className="text-[11px] font-medium text-muted-foreground">
              Area level
            </label>
            <Input
              id={`level-${card.id}`}
              list={`level-${card.id}-options`}
              inputMode="numeric"
              value={draft}
              onChange={(event) => {
                const raw = event.target.value.replace(/[^0-9]/g, "");
                setDraft(raw);
                commit(raw);
              }}
              onBlur={() => {
                if (draft.trim() === "" || Number(draft) < card.minLevel) setDraft(String(level));
              }}
              className="h-7 w-20 text-xs"
            />
            <datalist id={`level-${card.id}-options`}>
              {filteredLevels.map((entry) => (
                <option key={entry} value={entry} />
              ))}
            </datalist>
            <span className="text-[11px] text-muted-foreground">
              any area level {card.minLevel}+ · biome does not restrict spawns
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Average XP per kill</span>
            <span className="text-lg font-bold tabular-nums text-violet-400">{formatNumber(xpPerKill(card, level))}</span>
          </div>
          <div>
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Average XP per stat
            </span>
            <div className="mt-1 flex flex-wrap gap-1">
              {statEntries.map((entry) => (
                <span
                  key={entry.stat}
                  className="inline-flex items-center gap-1 rounded border border-border bg-muted/50 px-1.5 py-px text-xs"
                  title={entry.label}
                >
                  {entry.icon ? (
                    <img src={entry.icon} alt={entry.label} className="h-4 w-4 object-contain [image-rendering:pixelated]" />
                  ) : (
                    <span className="text-[10px] text-muted-foreground">{entry.label}</span>
                  )}
                  <span className="font-semibold tabular-nums">{formatNumber(entry.value)}</span>
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MonsterTile({ card, animal = false }: { card: MonsterCard; animal?: boolean }) {
  return (
    <Card className="overflow-hidden">
      <MonsterArt card={card} />
      <div className="flex items-center gap-2 px-2.5 pt-2">
        <span className="truncate text-base font-bold">{card.name}</span>
        <span className="text-[11px] text-muted-foreground">#{card.id}</span>
        <span
          className={`ml-auto rounded-full border px-2 text-[11px] ${
            animal ? "border-amber-500/40 bg-amber-500/10 text-amber-300" : "border-violet-500/40 bg-violet-500/10 text-violet-300"
          }`}
        >
          {animal ? "Animal" : "Combat"}
        </span>
      </div>
      <Chips card={card} />
      <BoxBlock card={card} animal={animal} />
      {animal ? (
        <p className="mt-2 border-t border-border/60 px-2.5 py-1.5 text-[11px] text-muted-foreground">
          Not fought in battle, so there is no XP per kill.
        </p>
      ) : (
        <XpBlock card={card} />
      )}
    </Card>
  );
}

function TileGrid({ cards, animal = false }: { cards: MonsterCard[]; animal?: boolean }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {cards.map((card) => (
        <div key={`${card.id}-${card.terrainCode}`} id={`monster-${card.id}`}>
          <MonsterTile card={card} animal={animal} />
        </div>
      ))}
    </div>
  );
}

export default function MonstersPage() {
  const params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.search);
  const focus = params.get("monster") ?? "";
  const [query, setQuery] = useState(params.get("search") ?? focus);
  const [showNote, setShowNote] = useState(false);

  const combat = useMemo(() => filterMonsterCards(COMBAT_MONSTER_CARDS, query), [query]);
  const animals = useMemo(() => filterMonsterCards(ANIMAL_MONSTER_CARDS, query), [query]);
  const total = COMBAT_MONSTER_CARDS.length + ANIMAL_MONSTER_CARDS.length;

  return (
    <div className="min-h-screen bg-background transition-colors">
      <div className="mx-auto max-w-6xl px-4 py-6">
        <div className="mb-3 flex items-center justify-between">
          <h1 className="flex items-center gap-2 text-xl font-bold text-foreground">
            <Skull className="h-5 w-5 text-violet-500" />
            Monster Spawns &amp; Loot
          </h1>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setShowNote((value) => !value)}
              className="h-8 w-8 text-muted-foreground"
              title="Page notes"
            >
              <Info className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>

        <p className="mb-3 max-w-3xl text-sm text-muted-foreground">
          One card per monster: where it spawns, the cave that spawns it, what its treasure box contains and the
          average XP per kill.
        </p>

        <div className="mb-3 rounded-lg border border-border bg-muted/15 px-3 py-2 text-xs text-muted-foreground">
          Spawn biome and level range come from <code>KA GameData - Monster.csv</code>. A cave spawns monsters whose
          terrain matches the cave and whose level range contains the area level (recovered from the game code), so the
          cave level shown is the monster&apos;s own lowest level.
        </div>

        {showNote && (
          <div className="mb-3">
            <textarea
              defaultValue={localStorage.getItem("ka_note_monsters") ?? ""}
              onBlur={(event) => localStorage.setItem("ka_note_monsters", event.target.value)}
              placeholder="Personal notes for this page… (only visible to you, saved on this device)"
              className="h-20 w-full resize-none rounded-md border border-input bg-muted/20 px-3 py-2 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
        )}

        <div className="relative mb-3">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search monsters, biomes, caves or dropped items"
            className="h-9 pl-9 text-sm"
          />
        </div>

        <div className="mb-1.5 flex items-baseline justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Combat monsters</h2>
          <span className="text-xs text-muted-foreground">
            {query ? `${combat.length} of ${COMBAT_MONSTER_CARDS.length}` : COMBAT_MONSTER_CARDS.length}
          </span>
        </div>
        {combat.length ? (
          <TileGrid cards={combat} />
        ) : (
          <Card className="p-6 text-sm text-muted-foreground">No combat monsters match that search.</Card>
        )}

        <div className="mb-1.5 mt-8 flex items-baseline justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Farmable animals</h2>
          <span className="text-xs text-muted-foreground">
            {query ? `${animals.length} of ${ANIMAL_MONSTER_CARDS.length}` : ANIMAL_MONSTER_CARDS.length}
          </span>
        </div>
        <p className="mb-2 max-w-3xl text-xs text-muted-foreground">
          Not fought in battle and not spawned by caves. Their row is a material yield, listed here so the treasure
          contents are still searchable.
        </p>
        {animals.length ? (
          <TileGrid cards={animals} animal />
        ) : (
          <Card className="p-6 text-sm text-muted-foreground">No animals match that search.</Card>
        )}

        <p className="mt-6 text-center text-xs text-muted-foreground">
          {total} monster rows · {COMBAT_MONSTER_CARDS.length} combat · {ANIMAL_MONSTER_CARDS.length} farmable
        </p>
      </div>
    </div>
  );
}
