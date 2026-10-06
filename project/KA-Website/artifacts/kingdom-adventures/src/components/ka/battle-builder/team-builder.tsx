import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Copy, Plus, Search, Trash2, UserPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SearchableSelect } from "@/components/searchable-select";
import { DifficultyBadge, RankBadge } from "@/components/ka/badges";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { getSkillIcon } from "@/lib/skill-icons";
import { getStatIcon } from "@/lib/stat-icons";
import {
  ENCOUNTER_FAMILIES,
  ENCOUNTER_VARIANTS,
  JOB_CATALOG,
  JOB_BY_ID,
  MONSTER_BY_ID,
  difficultyName,
  type BattleEncounterVariant,
} from "@/lib/battle-setup";
import type { SavedLoadout } from "@/lib/battle-legality";
import { dropUnsupportedHumanEquipment } from "@/lib/battle-picker-rules";
import { MONSTER_ICON_MAP } from "@/lib/monster-icons";
import {
  BUILDER_EQUIPMENT_SLOTS,
  createDraftCharacter,
  draftStatRows,
  draftCharacterFromLoadout,
  gearInSlot,
  moveInList,
  removeAt,
  statShortLabel,
  type BuilderSharedData,
  type DraftCharacter,
} from "@/lib/battle-team-draft";
import {
  CharacterSprite,
  CharacterSpriteThumb,
  GearSlotEditors,
  PetSlotIconStrip,
  PetSlotsSection,
  SkillSlotEditor,
  StatLevelEditor,
} from "./editors";

/* ------------------------------------------------------------------ */
/* 1 - Encounter (comes first)                                         */
/* ------------------------------------------------------------------ */

/**
 * Presentation-only helpers for the encounter tiles. The family label comes from the recovered table
 * and is long ("Saturday - Kairobot Knight's Challenge"); the tile shows the part after the weekday so
 * the card stays readable. The boss art is the monster's own recovered sprite (`MONSTER_BY_ID.src`),
 * with the wiki icon map as the existing fallback - no new game data is introduced.
 */
function shortFamilyName(name: string): string {
  const separator = name.indexOf(" - ");
  return separator === -1 ? name : name.slice(separator + 3);
}

function encounterBoss(entry: BattleEncounterVariant): { name: string | null; art: string | null } {
  const boss = MONSTER_BY_ID.get(entry.boss.monsterId);
  if (!boss) return { name: null, art: null };
  return { name: boss.name, art: boss.src || MONSTER_ICON_MAP[boss.name] || null };
}

/**
 * Recovered titles carry the difficulty as a trailing "(Hard)" suffix; the card and the Selected
 * row show the difficulty once as a badge, so drop the suffix when it exactly matches the name.
 */
function encounterTitle(entry: BattleEncounterVariant): string {
  const suffix = ` (${entry.difficultyName})`;
  return entry.title.endsWith(suffix) ? entry.title.slice(0, -suffix.length) : entry.title;
}

export function EncounterSection({
  encounterId,
  onSelect,
}: {
  encounterId: number;
  onSelect: (encounterId: number) => void;
}) {
  const current = ENCOUNTER_VARIANTS.find((entry) => entry.id === encounterId) ?? ENCOUNTER_VARIANTS[0];
  // Start on the selected encounter's own family instead of the 20-card "All" list; "All" stays
  // selectable, so every encounter remains reachable.
  const [family, setFamily] = useState<string>(() => current.familyId);
  const [query, setQuery] = useState("");

  // Keep the selected encounter inside the visible list when the page switches encounter.
  useEffect(() => {
    if (family !== "all" && current.familyId !== family) setFamily(current.familyId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.familyId]);

  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    return ENCOUNTER_VARIANTS.filter((entry) => {
      if (family !== "all" && entry.familyId !== family) return false;
      if (!text) return true;
      return (
        entry.title.toLowerCase().includes(text) ||
        entry.familyName.toLowerCase().includes(text) ||
        difficultyName(entry.difficulty).toLowerCase().includes(text) ||
        String(entry.id) === text
      );
    });
  }, [family, query]);

  return (
    <Card data-builder-encounter>
      <CardHeader>
        <CardTitle>1 - Pick an encounter</CardTitle>
        <CardDescription>Who you fight first. The team is built and run against this encounter.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex min-w-[12rem] flex-1 items-center gap-2 rounded border bg-background px-2 py-1">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input
              aria-label="Search encounters"
              placeholder="Search encounters..."
              className="w-full bg-transparent text-sm outline-none"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap gap-1" role="tablist" aria-label="Encounter families">
            <button
              type="button"
              role="tab"
              aria-selected={family === "all"}
              className={`rounded border px-2 py-1 text-xs font-medium ${family === "all" ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted"}`}
              onClick={() => setFamily("all")}
            >
              All
            </button>
            {ENCOUNTER_FAMILIES.map((entry) => (
              <button
                key={entry.id}
                type="button"
                role="tab"
                aria-selected={family === entry.id}
                className={`rounded border px-2 py-1 text-xs font-medium ${family === entry.id ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted"}`}
                onClick={() => setFamily(entry.id)}
                title={entry.name}
              >
                {shortFamilyName(entry.name)}
              </button>
            ))}
          </div>
        </div>

        <div className="grid max-h-72 grid-cols-1 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((entry: BattleEncounterVariant) => {
            const selected = entry.id === encounterId;
            const boss = encounterBoss(entry);
            return (
              <button
                key={entry.id}
                type="button"
                onClick={() => onSelect(entry.id)}
                aria-pressed={selected}
                data-encounter-option={entry.id}
                data-difficulty={entry.difficulty}
                data-encounter-boss={entry.boss.monsterId}
                className={`flex items-center gap-2 rounded-lg border p-1.5 text-left transition-colors ${selected ? "border-primary bg-primary/10" : "bg-card hover:border-primary/60 hover:bg-muted"}`}
              >
                <span className="ka-encounter-art" data-encounter-art={boss.art ? "recovered" : "missing"}>
                  {boss.art ? (
                    <img src={boss.art} alt="" className="ka-encounter-art__img" />
                  ) : (
                    <span className="ka-encounter-art__empty">?</span>
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-1">
                    <span className="truncate text-xs font-semibold">{encounterTitle(entry)}</span>
                    <DifficultyBadge difficulty={entry.difficultyName} className="shrink-0 text-[9px]" />
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                    {boss.name ? `${boss.name} · ` : ""}
                    {entry.enemyTotal} enemies · Lv {entry.levelField} · {shortFamilyName(entry.familyName)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
        {visible.length === 0 ? <p className="py-4 text-center text-sm text-muted-foreground">No encounters match your search.</p> : null}

        <div className="flex flex-wrap items-center gap-2 text-xs" data-selected-encounter={current.id}>
          <span className="ka-hud-strip__label">Selected</span>
          <span className="font-medium">{encounterTitle(current)}</span>
          <DifficultyBadge difficulty={current.difficultyName} className="text-[9px]" />
          <span className="text-muted-foreground">
            {encounterBoss(current).name ?? "boss art not recovered"} · {current.enemyTotal} enemies ·{" "}
            {shortFamilyName(current.familyName)}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Presets (copied from the Loadout Builder)                           */
/* ------------------------------------------------------------------ */

function PresetSection({
  savedLoadouts,
  onCopy,
}: {
  savedLoadouts: SavedLoadout[];
  onCopy: (loadout: SavedLoadout) => void;
}) {
  const usable = savedLoadouts.filter((entry) => entry && typeof entry === "object");
  return (
    <div className="space-y-1.5" data-builder-presets>
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        Saved characters
      </span>
      {usable.length === 0 ? (
        <p className="text-[11px] text-muted-foreground/70">
          No saved characters yet.
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {usable.map((loadout, index) => (
            <button
              key={loadout.id ?? `${loadout.name ?? "loadout"}-${index}`}
              type="button"
              onClick={() => onCopy(loadout)}
              className="flex min-h-11 items-center gap-1.5 rounded border bg-card px-3 py-2 text-xs hover:border-primary hover:bg-muted"
              data-preset-option={loadout.id ?? index}
              title="Add a copy of this character to the team"
            >
              <Copy className="h-3 w-3" />
              {loadout.name?.trim() || loadout.jobName?.trim() || `Loadout ${index + 1}`}
              {loadout.rank ? <RankBadge rank={loadout.rank} /> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Team member editor                                                  */
/* ------------------------------------------------------------------ */

function CharacterSummary({ character, data }: { character: DraftCharacter; data: BuilderSharedData | null }) {
  const stats = useMemo(() => draftStatRows(character, data), [character, data]);
  const equipment = BUILDER_EQUIPMENT_SLOTS.flatMap(({ slot }) => {
    const item = gearInSlot(character, slot, data?.slotAssignments);
    return item ? [{ ...item, slot }] : [];
  });
  const skills = character.skills ?? [];

  return (
    <div className="hidden min-w-0 flex-1 grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_minmax(0,1.25fr)] gap-3 min-[1100px]:grid" data-character-summary>
      <div className="min-w-0">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Stat levels</span>
        <div className="grid grid-cols-4 gap-x-1 gap-y-1" aria-label="Stat levels">
          {stats.map(({ stat, level }) => {
            const icon = getStatIcon(data?.statIcons, stat);
            const label = statShortLabel(stat);
            return (
              <span key={stat} className="flex min-w-0 items-center gap-0.5 text-xs tabular-nums" title={`${label} level ${level}`} aria-label={`${label} level ${level}`}>
                {icon ? <img src={icon} alt="" className="h-4 w-4 shrink-0 object-contain" /> : <span className="text-muted-foreground">{label}</span>}
                <span className="truncate">{level}</span>
              </span>
            );
          })}
        </div>
      </div>
      <div className="min-w-0">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Equipment</span>
        <div className="flex flex-wrap gap-x-2 gap-y-0.5" aria-label="Equipped gear">
          {equipment.length ? equipment.map((item) => {
            const icon = getEquipmentIcon(data?.equipIcons, item.name);
            return (
              <span key={item.slot} className="flex min-w-0 max-w-full items-center gap-0.5 text-[11px]" title={`${item.name}, level ${item.level}`} aria-label={`${item.name}, level ${item.level}`}>
                {icon ? <img src={icon} alt="" className="h-5 w-5 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} /> : null}
                <span className="max-w-28 truncate">{item.name}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">Lv{item.level}</span>
              </span>
            );
          }) : <span className="text-[10px] text-muted-foreground">None</span>}
        </div>
      </div>
      <div className="min-w-0">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Skills</span>
        <div className="flex flex-wrap gap-x-2 gap-y-0.5" aria-label="Equipped skills">
          {skills.length ? skills.map((skill, index) => {
            const icon = getSkillIcon(skill);
            return (
              <span key={`${skill}-${index}`} className="flex min-w-0 max-w-full items-center gap-0.5 text-[11px]" title={skill}>
                {icon ? <img src={icon} alt="" className="h-5 w-5 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} /> : null}
                <span className="max-w-28 truncate">{skill}</span>
              </span>
            );
          }) : <span className="text-[10px] text-muted-foreground">None</span>}
        </div>
      </div>
    </div>
  );
}

function CharacterCard({
  character,
  index,
  count,
  data,
  allSkills,
  onChange,
  onRemove,
  onSave,
  onMove,
}: {
  character: DraftCharacter;
  index: number;
  count: number;
  data: BuilderSharedData | null;
  allSkills: string[];
  onChange: (next: DraftCharacter) => void;
  onRemove: () => void;
  onSave: () => void;
  onMove: (direction: -1 | 1) => void;
}) {
  const job = JOB_BY_ID.get(character.jobName ?? "");
  const ranks = useMemo(() => {
    const fromShared = data?.jobs?.[character.jobName ?? ""]?.ranks;
    const keys = fromShared ? Object.keys(fromShared) : job?.ranks ?? ["D", "C", "B", "A", "S"];
    return keys.sort();
  }, [data, character.jobName, job]);
  const [open, setOpen] = useState(index === 0);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const weapon = gearInSlot(character, "weapon", data?.slotAssignments);
  const changeJob = (jobName: string) => {
    const nextJob = JOB_BY_ID.get(jobName);
    const updated = {
      ...character,
      jobName,
      rank: nextJob && !nextJob.ranks.includes((character.rank ?? "D") as never) ? nextJob.ranks[0] ?? "D" : character.rank,
    };
    onChange(data ? dropUnsupportedHumanEquipment(updated, data) : updated);
  };

  return (
    <div className="rounded-lg border bg-card" data-builder-character={index} data-character-id={character.id}>
      <div className="flex flex-wrap items-center gap-2 border-b px-2 py-1.5">
        <span className="flex h-5 w-5 items-center justify-center rounded bg-muted text-[10px] font-semibold" data-team-slot={index}>
          {index + 1}
        </span>
        {/* Collapsed row identity: the equipped portrait plus the declared pets, both live from the
            same draft the expanded editor edits. */}
        <CharacterSpriteThumb character={character} data={data} />
        <span className="flex min-w-0 flex-1 flex-col min-[1100px]:w-44 min-[1100px]:flex-none">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-sm font-semibold">{character.name?.trim() || `Character ${index + 1}`}</span>
            {character.rank ? <RankBadge rank={character.rank} /> : null}
          </span>
          <span className="truncate text-[11px] text-muted-foreground">
            {character.jobName || "No job"}
            {weapon ? <span className="min-[1100px]:hidden"> · {weapon.name}</span> : null}
          </span>
        </span>
        <PetSlotIconStrip pets={character.householdPets ?? []} />
        <CharacterSummary character={character} data={data} />
        <span className="ml-auto flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={() => onMove(-1)}
            disabled={index === 0}
            className="flex h-10 w-10 items-center justify-center rounded text-muted-foreground hover:text-foreground disabled:opacity-30"
            title="Move earlier in the team order"
            aria-label="Move unit up"
            data-action="move-unit-up"
          >
            <ArrowUp className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={index === count - 1}
            className="flex h-10 w-10 items-center justify-center rounded text-muted-foreground hover:text-foreground disabled:opacity-30"
            title="Move later in the team order"
            aria-label="Move unit down"
            data-action="move-unit-down"
          >
            <ArrowDown className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            className="min-h-10 rounded px-2 text-xs text-muted-foreground hover:text-foreground"
            data-action="toggle-unit"
          >
            {open ? "Close" : "Edit"}
          </button>
          <button type="button" onClick={onSave} className="flex min-h-11 items-center justify-center gap-1 rounded px-2 text-xs text-primary hover:bg-primary/10" title="Add a copy to Saved characters" aria-label="Copy to Characters" data-action="save-character">
            <Copy className="h-4 w-4" /> Copy to Characters
          </button>
          <button
            type="button"
            onClick={() => setConfirmRemove(true)}
            className="flex h-10 w-10 items-center justify-center rounded text-destructive hover:bg-destructive/10"
            title="Remove unit"
            aria-label="Remove unit"
            data-action="remove-unit"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>

      {confirmRemove ? (
        <div className="flex flex-wrap items-center justify-end gap-2 border-b px-3 py-2 text-xs" data-confirm-remove-unit>
          <span className="mr-auto">Remove {character.name || "this character"}?</span>
          <Button type="button" variant="outline" className="min-h-11" onClick={() => setConfirmRemove(false)}>Cancel</Button>
          <Button type="button" variant="destructive" className="min-h-11" onClick={onRemove}>Remove</Button>
        </div>
      ) : null}

      {open ? (
        <div className="space-y-3 p-2.5">
          <div className="flex flex-wrap items-start gap-3">
            <CharacterSprite character={character} data={data} />
            <div className="min-w-[14rem] flex-1 space-y-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <Input
                  value={character.name ?? ""}
                  onChange={(event) => onChange({ ...character, name: event.target.value })}
                  className="h-7 w-40 text-xs"
                  aria-label="Character name"
                />
                <SearchableSelect
                  value={character.jobName ?? ""}
                  onChange={changeJob}
                  options={JOB_CATALOG.map((entry) => ({ value: entry.id, label: entry.name }))}
                  placeholder="Job..."
                  triggerClassName="h-7 w-40 text-xs"
                />
                <SearchableSelect
                  value={character.rank ?? ""}
                  onChange={(rank) => onChange({ ...character, rank })}
                  options={ranks.map((rank) => ({ value: rank, label: rank }))}
                  placeholder="Rank..."
                  triggerClassName="h-7 w-20 text-xs"
                />
                <div className="flex overflow-hidden rounded border border-input" role="group" aria-label="Gender">
                  {[
                    { value: 0, label: "Male" },
                    { value: 1, label: "Female" },
                  ].map((option) => (
                    <button
                      key={option.label}
                      type="button"
                      aria-pressed={(character.gender ?? 0) === option.value}
                      onClick={() => onChange({ ...character, gender: option.value })}
                      className={`px-2 py-0.5 text-[11px] font-medium ${(character.gender ?? 0) === option.value ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:text-foreground"}`}
                      data-gender={option.value}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <label className="flex items-center gap-1 text-[11px] text-muted-foreground" title="Declared native grow/awakening steps (native limit +30 per step).">
                  Awaken
                  <Input
                    type="text"
                    inputMode="numeric"
                    value={character.awakening === undefined ? "" : String(character.awakening)}
                    onChange={(event) => {
                      const raw = event.target.value.trim();
                      onChange({ ...character, awakening: raw === "" ? undefined : Math.max(0, Number(raw) || 0) });
                    }}
                    className="h-6 w-12 text-right text-xs tabular-nums"
                    aria-label="Awakening steps"
                  />
                </label>
              </div>
              <div>
                <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Gear</span>
                <GearSlotEditors character={character} data={data} onChange={onChange} />
              </div>
            </div>
          </div>

          <div>
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Stats <span className="normal-case font-normal">(current value incl. gear, editable training level)</span>
            </span>
            <StatLevelEditor character={character} data={data} onChange={onChange} />
          </div>

          <div>
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Skills</span>
            {!character.jobName ? (
              <SearchableSelect
                value=""
                onChange={changeJob}
                options={JOB_CATALOG.map((entry) => ({ value: entry.id, label: entry.name }))}
                placeholder="Choose a job to add skills"
                triggerClassName="min-h-11 text-sm"
              />
            ) : null}
            <SkillSlotEditor
              skills={character.skills ?? []}
              invocations={(character.skills ?? []).map((_, i) => character.skillInvocations?.[i])}
              allSkills={allSkills}
              onChange={(skills, invocations) => {
                const updated = {
                  ...character,
                  skills,
                  skillInvocations: invocations.map((level) => (level === 0 || level === 1 || level === 2 ? level : undefined)) as number[],
                };
                onChange(data ? dropUnsupportedHumanEquipment(updated, data) : updated);
              }}
              idPrefix={character.id}
              jobName={character.jobName}
            />
          </div>

          <PetSlotsSection character={character} allSkills={allSkills} onPetsChange={(pets) => onChange({ ...character, householdPets: pets })} />
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 3 - Ordered team                                                    */
/* ------------------------------------------------------------------ */

export function TeamSection({
  characters,
  data,
  allSkills,
  savedLoadouts,
  onSaveCharacter,
  onChange,
}: {
  characters: DraftCharacter[];
  data: BuilderSharedData | null;
  allSkills: string[];
  savedLoadouts: SavedLoadout[];
  onSaveCharacter: (character: DraftCharacter) => void;
  onChange: (next: DraftCharacter[]) => void;
}) {
  const humans = characters.length;
  return (
    <Card data-builder-team>
      <CardHeader>
        <CardTitle>3 - Your team, in order</CardTitle>
        <CardDescription>
          Team edits are kept on this device. Copy to Characters adds a reusable copy to Saved characters.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <PresetSection savedLoadouts={savedLoadouts} onCopy={(loadout) => onChange([...characters, draftCharacterFromLoadout(loadout, characters.length + 1)])} />
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="min-h-11 gap-1 text-xs"
            onClick={() => onChange([...characters, { ...createDraftCharacter(characters.length + 1), jobName: "", rank: "" }])}
            data-action="create-character"
          >
            <UserPlus className="h-3 w-3" /> Create character
          </Button>
          {/*
            Party size, in the recovered native terms: INIT_MAX_BOSS_BATTLE_MEMBERS_NUM /
            INIT_MAX_DUNGEON_CONQUEST_MEMBERS_NUM are 2 (the initial members a battle starts with),
            while BossConquestSystem.GetMaxMembersNum / DungeonConquestSystem.GetMaxMembersNum return
            ValuableSystem.GetSpEffect(4 or 5) + 2 (the Military Training Manual for boss battles /
            Legendary Expedition Map for cave conquests). This site cannot read owned valuable
            effects, so the real formation cap is unknown here and a bigger roster is only flagged.
          */}
          <span className="text-xs text-muted-foreground" data-team-count={humans}>{humans} character(s)</span>
        </div>

        {characters.length === 0 ? (
          <p className="rounded border border-dashed p-4 text-center text-xs text-muted-foreground">
            No units yet - copy a preset above or create a character.
          </p>
        ) : (
          <div className="space-y-2">
            {characters.map((character, index) => (
              <CharacterCard
                key={character.id}
                character={character}
                index={index}
                count={characters.length}
                data={data}
                allSkills={allSkills}
                onChange={(next) => onChange(characters.map((entry, i) => (i === index ? next : entry)))}
                onRemove={() => onChange(removeAt(characters, index))}
                onSave={() => onSaveCharacter(character)}
                onMove={(direction) => onChange(moveInList(characters, index, index + direction))}
              />
            ))}
          </div>
        )}

        {humans > 0 ? (
          <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground/70">
            <Plus className="h-3 w-3" />
            Team list order is preserved; battle starting cells are shown above.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
