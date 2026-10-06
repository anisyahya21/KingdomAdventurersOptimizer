import { useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, House, Plus, Search, Trash2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SearchableSelect } from "@/components/searchable-select";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import { getEquipmentIcon, getFacilityIcon } from "@/lib/equipment-icons";
import { getSkillIcon } from "@/lib/skill-icons";
import { getStatIcon } from "@/lib/stat-icons";
import { MONSTER_ICON_MAP } from "@/lib/monster-icons";
import { canSetSkill } from "@/game-data/skill-job-admission";
import { canPickHumanEquipment, canPickHumanSkill } from "@/lib/battle-picker-rules";
import { STAT_COLUMNS } from "@/game-data/stat-parameter-ids";
import { MONSTER_CATALOG, MONSTER_INNATE_SKILL_BY_ID, MONSTER_PARAMETER_IDS, SKILL_BY_ID } from "@/lib/battle-setup";
import { SKILL_ID_BY_NAME, renderSkillName, type SavedLoadoutPet } from "@/lib/battle-legality";
import {
  BUILDER_EQUIPMENT_SLOTS,
  INVOCATION_LEVELS,
  MAX_SKILL_SLOTS,
  PERMANENTLY_ACTIVE_LABEL,
  builderSkillTrigger,
  declaredHouseholdPets,
  draftStatRows,
  equipmentNamesForSlot,
  gearInSlot,
  invocationLevelOf,
  moveInList,
  petCapacityOverBy,
  petSlotCapacity,
  setGearInSlot,
  statFullLabel,
  statShortLabel,
  type BuilderSharedData,
  type DraftStatRow,
  type DraftCharacter,
} from "@/lib/battle-team-draft";

/* ------------------------------------------------------------------ */
/* Shared small pieces                                                 */
/* ------------------------------------------------------------------ */

/** Numeric text input that commits on change but never emits NaN. */
export function NumberField({
  value,
  onChange,
  min = 1,
  max = 999,
  className = "h-7 w-16 text-right text-xs tabular-nums",
  ariaLabel,
}: {
  value: number | undefined;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
  className?: string;
  ariaLabel: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const cancelCommit = useRef(false);
  const commit = (raw: string) => {
    const parsed = Number(raw);
    if (raw.trim() !== "" && Number.isFinite(parsed)) {
      onChange(Math.max(min, Math.min(max, Math.trunc(parsed))));
    }
    setDraft(null);
  };
  return (
    <Input
      type="text"
      inputMode="numeric"
      aria-label={ariaLabel}
      className={className}
      value={draft ?? (value === undefined ? "" : String(value))}
      onChange={(event) => setDraft(event.target.value.replace(/[^\d]/g, ""))}
      onBlur={(event) => { if (cancelCommit.current) { cancelCommit.current = false; setDraft(null); } else commit(event.target.value); }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") { cancelCommit.current = true; setDraft(null); event.currentTarget.blur(); }
      }}
    />
  );
}

/* ------------------------------------------------------------------ */
/* Character sprite (wielding the declared gear)                       */
/* ------------------------------------------------------------------ */

/**
 * The player sprite drawn from the SAME inputs the Loadout Builder preview uses: job name, rank,
 * gender variant, weapon and shield. Gear changes are reflected immediately because the weapon and
 * shield come from the edited slot selections rather than a copy.
 */
export function CharacterSprite({ character, data }: { character: DraftCharacter; data: BuilderSharedData | null }) {
  const [equipState, setEquipState] = useState<"right" | "up">("right");
  const gender = character.gender === 1 ? 2 : 1;
  const weapon = gearInSlot(character, "weapon", data?.slotAssignments);
  const shield = gearInSlot(character, "shield", data?.slotAssignments);
  return (
    <div className="flex flex-col items-center gap-1.5" data-builder-sprite>
      <CharacterPreviewCanvas
        jobName={character.jobName ?? "Guard"}
        rank={character.rank}
        variant={gender as 1 | 2}
        equipState={equipState}
        weaponName={weapon?.name ?? null}
        shieldName={shield?.name ?? null}
        scale={4}
        poseFrame={0}
        label={`${character.name ?? "Character"} sprite`}
        className="rounded"
      />
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => setEquipState("right")}
          className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${equipState === "right" ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}
        >
          Right
        </button>
        <button
          type="button"
          onClick={() => setEquipState("up")}
          className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${equipState === "up" ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}
        >
          Up
        </button>
      </div>
      <p className="max-w-[9rem] text-center text-[10px] leading-tight text-muted-foreground">
        Weapon: {weapon?.name ?? "none"} · Shield: {shield?.name ?? "none"}
      </p>
    </div>
  );
}

/**
 * Compact variant of {@link CharacterSprite} for the collapsed team-builder row. It draws from the
 * same inputs (job, rank, gender variant, weapon and shield pulled from the edited slot selections),
 * so the thumbnail mirrors the expanded editor as gear/job/rank/gender change. There is no local
 * state here - the row just needs a small always-current portrait, with the full summary as tooltip.
 */
export function CharacterSpriteThumb({
  character,
  data,
  className,
}: {
  character: DraftCharacter;
  data: BuilderSharedData | null;
  className?: string;
}) {
  const gender = character.gender === 1 ? 2 : 1;
  const weapon = gearInSlot(character, "weapon", data?.slotAssignments);
  const shield = gearInSlot(character, "shield", data?.slotAssignments);
  const jobName = character.jobName ?? "Guard";
  const name = character.name?.trim() || "Character";
  const summary = `${name} · ${jobName}${character.rank ? ` ${character.rank}` : ""} · Weapon: ${weapon?.name ?? "none"} · Shield: ${shield?.name ?? "none"}`;
  return (
    <span className="inline-flex shrink-0 items-center" title={summary} data-row-sprite>
      <CharacterPreviewCanvas
        jobName={jobName}
        rank={character.rank}
        variant={gender as 1 | 2}
        equipState="right"
        weaponName={weapon?.name ?? null}
        shieldName={shield?.name ?? null}
        scale={2}
        poseFrame={0}
        label={summary}
        className={className ?? "h-16 w-auto"}
      />
    </span>
  );
}

/**
 * Compact occupied-slot strip for the collapsed team-builder row: one icon per declared household
 * pet, drawn from the same canonical monster art the editor's {@link MonsterIconPicker} uses. Empty
 * attributed slots are an editing concern and stay in the expanded editor; this only shows the pets
 * the resident actually brings, each with a readable name/level tooltip.
 */
export function PetSlotIconStrip({ pets }: { pets: SavedLoadoutPet[] }) {
  if (pets.length === 0) return null;
  return (
    <span className="flex flex-wrap items-center gap-1" data-row-pets data-row-pet-count={pets.length}>
      {pets.map((pet, index) => {
        const species = MONSTER_CATALOG.find((entry) => entry.id === pet.monsterId);
        const icon = species ? monsterIcon(species) : undefined;
        const label = pet.name?.trim() || species?.name || `Pet ${index + 1}`;
        const accessible = `${label}, level ${pet.level ?? 1}`;
        return (
          <span
            key={`${pet.monsterId}-${index}`}
            role="img"
            aria-label={accessible}
            title={accessible}
            className="flex h-10 w-10 items-center justify-center overflow-hidden rounded border border-border/60 bg-muted/20"
          >
            {icon ? (
              <img src={icon} alt="" className="h-8 w-8 object-contain" style={{ imageRendering: "pixelated" }} />
            ) : (
              <span className="text-[9px] font-semibold text-muted-foreground">?</span>
            )}
          </span>
        );
      })}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Stats                                                               */
/* ------------------------------------------------------------------ */

/**
 * One stat row: the canonical icon and short label, the converted value, the equipped items'
 * share of it, and the declared training level.
 */
function StatLevelRow({
  row,
  icon,
  onChangeLevel,
}: {
  row: DraftStatRow;
  icon?: string;
  onChangeLevel: (stat: string, level: number) => void;
}) {
  const { stat, level, curveValue, valuableValue, equipmentValue, total } = row;
  const breakdown = [
    curveValue === null ? "no job-curve row for this job/rank" : `job curve ${curveValue}`,
    valuableValue !== 0 ? `valuables ${valuableValue > 0 ? "+" : ""}${valuableValue}` : null,
    equipmentValue !== 0 ? `gear ${equipmentValue > 0 ? "+" : ""}${equipmentValue}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <label
      className="flex min-w-0 items-center justify-between gap-1.5 rounded border border-border/60 bg-muted/20 px-1.5 py-1"
      data-builder-stat={stat}
      title={`${statFullLabel(stat)} at level ${level}: ${breakdown}`}
    >
      <span className="flex min-w-0 items-center gap-1 text-[11px] font-medium" title={statFullLabel(stat)}>
        {icon ? <img src={icon} alt="" className="h-3.5 w-3.5 shrink-0 object-contain" /> : null}
        <span className="truncate">{statShortLabel(stat)}</span>
      </span>
      <span className="flex shrink-0 items-center gap-1">
        {equipmentValue !== 0 ? (
          <span
            className="text-[9px] tabular-nums text-emerald-600"
            data-stat-gear={stat}
            title={`Equipped items add ${equipmentValue > 0 ? "+" : ""}${equipmentValue} to ${statFullLabel(stat)}`}
          >
            {equipmentValue > 0 ? "+" : ""}
            {equipmentValue}
          </span>
        ) : null}
        <span className="w-16 text-right text-xs font-semibold tabular-nums" data-stat-value={stat}>
          {total === null ? "?" : total.toLocaleString("en-US")}
        </span>
        <NumberField
          value={level}
          onChange={(next) => onChangeLevel(stat, next)}
          className="h-6 w-16 text-right text-xs tabular-nums"
          ariaLabel={`${statFullLabel(stat)} level`}
        />
      </span>
    </label>
  );
}

/**
 * Per-stat level editor. Levels are a DECLARED input: the conversion
 * (`battle-legality.loadoutParameters`) turns `level` through the job curve into native raw
 * parameters. The shown value is that same conversion result plus the equipped items'
 * contribution (`draftStatRows`), so the number here is the number the run uses - not a second,
 * page-local formula.
 *
 * Layout only: the rows keep `draftStatRows` order and are arranged in the canonical 3 / 4 / 5
 * status-screen columns (`STAT_COLUMNS`) - HP/MP/Vig, Atk/Def/Spd/Lck, Int/Dex/Gth/Mov/Hrt. Three
 * columns from 640px up; below that the same grouping stacks in one column so a narrow card cannot
 * overflow.
 */
export function StatLevelEditor({
  character,
  data,
  onChange,
}: {
  character: DraftCharacter;
  data: BuilderSharedData | null;
  onChange: (next: DraftCharacter) => void;
}) {
  const rows = useMemo(() => draftStatRows(character, data), [character, data]);
  const rowByStat = useMemo(
    () => new Map<string, DraftStatRow>(rows.map((row): [string, DraftStatRow] => [row.stat, row])),
    [rows],
  );
  /** The canonical grouping, plus any stat the shared table does not place (a stat is never hidden). */
  const columns = useMemo(() => {
    const placed = new Set<string>(STAT_COLUMNS.flat());
    const unplaced = rows.filter((row) => !placed.has(row.stat)).map((row) => row.stat);
    return unplaced.length === 0 ? STAT_COLUMNS : [...STAT_COLUMNS, unplaced];
  }, [rows]);

  const setLevel = (stat: string, level: number) => {
    onChange({ ...character, statLevels: { ...(character.statLevels ?? {}), [stat]: level } });
  };

  return (
    <div
      className="grid grid-cols-1 gap-1.5 md:grid-cols-3 md:items-start md:gap-x-3"
      data-builder-stats
      data-builder-stat-columns="3-4-5"
    >
      {columns.map((column, columnIndex) => (
        <div
          key={columnIndex}
          className="flex min-w-0 flex-col gap-1.5"
          data-builder-stat-column={column.length}
        >
          {column.map((stat) => {
            const row = rowByStat.get(stat);
            return row ? <StatLevelRow key={stat} row={row} icon={getStatIcon(data?.statIcons, stat)} onChangeLevel={setLevel} /> : null;
          })}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Gear                                                                */
/* ------------------------------------------------------------------ */

export function GearSlotEditors({
  character,
  data,
  onChange,
}: {
  character: DraftCharacter;
  data: BuilderSharedData | null;
  onChange: (next: DraftCharacter) => void;
}) {
  return (
    <div className="space-y-1" data-builder-gear>
      {BUILDER_EQUIPMENT_SLOTS.map(({ slot, label }) => {
        const current = gearInSlot(character, slot, data?.slotAssignments);
        const options = equipmentNamesForSlot(data, slot).filter((name) => data && canPickHumanEquipment(character, data, name));
        const icon = current ? getEquipmentIcon(data?.equipIcons, current.name) : undefined;
        return (
          <div key={slot} className="flex items-center gap-1.5" data-builder-gear-slot={slot}>
            <span className="flex w-20 shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
              {icon ? <img src={icon} alt="" className="h-4 w-4 object-contain" style={{ imageRendering: "pixelated" }} /> : null}
              {label}
            </span>
            <SearchableSelect
              value={current?.name ?? ""}
              onChange={(name) => {
                if (name && (!data || !canPickHumanEquipment(character, data, name))) return;
                onChange(setGearInSlot(character, slot, name ? { name, level: current?.level ?? 1 } : null, data?.slotAssignments));
              }}
              options={[
                { value: "", label: "None" },
                ...options.map((name) => ({ value: name, label: name, icon: getEquipmentIcon(data?.equipIcons, name) })),
              ]}
              placeholder="None"
              triggerClassName="h-7 flex-1 text-xs"
            />
            <NumberField
              value={current?.level ?? 1}
              min={1}
              max={99}
              onChange={(level) =>
                current
                  ? onChange(setGearInSlot(character, slot, { name: current.name, level }, data?.slotAssignments))
                  : undefined
              }
              className="h-6 w-12 text-right text-xs tabular-nums"
              ariaLabel={`${label} level`}
            />
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Skills                                                              */
/* ------------------------------------------------------------------ */

/** Low / Normal / High trigger picker: the player-facing label for the skill's invocation level. */
function InvocationPicker({
  value,
  onChange,
  name,
}: {
  value: number | undefined;
  onChange: (next: number) => void;
  name: string;
}) {
  const active = invocationLevelOf(value);
  return (
    <div className="flex overflow-hidden rounded border border-input" role="group" aria-label={`${name} invocation`}>
      {INVOCATION_LEVELS.map((option) => (
        <button
          key={option.label}
          type="button"
          aria-pressed={active === option.value}
          onClick={() => onChange(option.value)}
          className={`min-h-10 min-w-11 px-1.5 text-[10px] font-medium ${active === option.value ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:text-foreground"}`}
          title={`Trigger: ${option.label}`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function skillAdmissionBadge(jobName: string | undefined, name: string, existing: number[]) {
  const skillId = SKILL_ID_BY_NAME.get(name);
  if (skillId === undefined) {
    return { label: "no catalog row", tone: "destructive" as const, title: "No recovered skill catalog row renders to this name." };
  }
  const verdict = canSetSkill({ jobName: jobName ?? "", skillId, existingSkillIds: existing });
  if (verdict.status === "admitted") return null;
  return {
    label: verdict.status === "rejected" ? "job-gate rejected" : "gate unknown",
    tone: verdict.status === "rejected" ? ("destructive" as const) : ("outline" as const),
    title: verdict.reason,
  };
}

/**
 * Ordered skill slots. An invoked skill gets a Low/Normal/High trigger; a permanently-active skill
 * (`battle-setup` `skillActivationStatus` -> SkillData.flags without FLAG_FOR_BATTLE 0x8) is shown
 * as "Always active" with no trigger, because the game has no activation level for it. The
 * `invocations` array stays the same length as `skills` in both cases.
 *
 * Every slot is the player's own declared skill and is freely reorderable/removable. A monster's
 * native first skill is NOT one of these slots: `PetSlotEditor` shows the copied canonical
 * Monster-table value as a separate innate row and exports it first, so no user skill is locked in
 * its place and none is mistaken for it.
 */
export function SkillSlotEditor({
  skills,
  invocations,
  allSkills,
  onChange,
  idPrefix,
  jobName,
  maxSkills = MAX_SKILL_SLOTS,
}: {
  skills: string[];
  invocations: Array<number | undefined>;
  allSkills: string[];
  onChange: (skills: string[], invocations: Array<number | undefined>) => void;
  idPrefix: string;
  jobName?: string;
  maxSkills?: number;
}) {
  const setInvocation = (index: number, level: number) => {
    const next = skills.map((_, i) => invocations[i]);
    next[index] = level;
    onChange([...skills], next);
  };
  const move = (index: number, direction: -1 | 1) => {
    const nextSkills = moveInList(skills, index, index + direction);
    if (nextSkills === skills) return;
    const nextInvocations = nextSkills.map((skill) => invocations[skills.indexOf(skill)]);
    onChange(nextSkills, nextInvocations);
  };
  const remove = (index: number) => {
    onChange(
      skills.filter((_, i) => i !== index),
      skills.map((_, i) => invocations[i]).filter((_, i) => i !== index),
    );
  };
  const add = (name: string) => {
    if (!name || skills.includes(name) || skills.length >= maxSkills) return;
    if (jobName !== undefined && !canPickHumanSkill(jobName, name, skills)) return;
    onChange([...skills, name], [...skills.map((_, i) => invocations[i]), 1]);
  };
  const availableSkills = allSkills.filter(
    (name) => !skills.includes(name) && (jobName === undefined || canPickHumanSkill(jobName, name, skills)),
  );

  return (
    <div className="space-y-1.5">
      <div className="space-y-1" data-skill-slots={idPrefix}>
        {skills.map((skill, index) => {
          const icon = getSkillIcon(skill);
          const admission = jobName !== undefined ? skillAdmissionBadge(jobName, skill, []) : null;
          const trigger = builderSkillTrigger(skill, invocations[index]);
          return (
            <div
              key={`${skill}-${index}`}
              className="flex flex-wrap items-center gap-1 rounded-md border border-border/60 bg-muted/20 px-1.5 py-1"
              data-skill-slot={index}
            >
              <span className="flex min-w-full items-center gap-1.5 sm:min-w-0 sm:flex-1">
                {icon ? (
                  <img src={icon} alt="" className="h-4 w-4 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                ) : null}
                <span className="truncate text-xs font-medium">{skill}</span>
                {admission ? (
                  <Badge variant={admission.tone} className="shrink-0 text-[9px]" title={admission.title}>
                    {admission.label}
                  </Badge>
                ) : null}
              </span>
              {trigger.status === "active" ? (
                <InvocationPicker value={invocations[index]} onChange={(level) => setInvocation(index, level)} name={skill} />
              ) : (
                <span
                  className="inline-flex min-h-10 items-center rounded border border-border/60 bg-muted/30 px-2 text-[10px] font-medium text-muted-foreground"
                  title={
                    trigger.status === "permanently_active"
                      ? "This skill is always active: SkillData.flags has no FLAG_FOR_BATTLE 0x8 bit, so no battle invocation applies and there is no High/Normal/Low trigger to choose (CanUseSkill 0x15df0e0; Entity.AddSkill 0x1472564 stores the native default level 1)."
                      : "No recovered SkillData row for this name, so the activation level cannot be resolved."
                  }
                  data-skill-trigger={trigger.status}
                >
                  {trigger.label}
                </span>
              )}
              <div className="flex w-full justify-end gap-1" aria-label={`${skill} order and removal`}>
              <button
                type="button"
                onClick={() => move(index, -1)}
                disabled={index === 0}
                className="flex h-10 w-10 items-center justify-center rounded text-muted-foreground hover:text-foreground disabled:opacity-30"
                title="Move up (higher priority)"
                aria-label={`Move ${skill} up`}
              >
                <ArrowUp className="h-3 w-3" />
              </button>
              <button
                type="button"
                onClick={() => move(index, 1)}
                disabled={index === skills.length - 1}
                className="flex h-10 w-10 items-center justify-center rounded text-muted-foreground hover:text-foreground disabled:opacity-30"
                title="Move down (lower priority)"
                aria-label={`Move ${skill} down`}
              >
                <ArrowDown className="h-3 w-3" />
              </button>
              <button
                type="button"
                onClick={() => remove(index)}
                className="flex h-10 w-10 items-center justify-center rounded text-destructive hover:bg-destructive/10 disabled:opacity-30"
                title="Remove skill"
                aria-label={`Remove ${skill}`}
              >
                <X className="h-3 w-3" />
              </button>
              </div>
            </div>
          );
        })}
        {skills.length === 0 ? (
          <span className="text-xs text-muted-foreground/60">No skills selected</span>
        ) : null}
      </div>
      {skills.length < maxSkills && availableSkills.length > 0 ? (
        <SearchableSelect
          value=""
          clearOnSelect
          onChange={(value) => {
            if (value) add(value);
          }}
          options={availableSkills.map((name) => ({ value: name, label: name, icon: getSkillIcon(name) }))}
          placeholder={skills.length === 0 ? "+ Add skill..." : "+ Add another skill..."}
          triggerClassName="min-h-11 text-sm"
        />
      ) : null}
      {skills.length < maxSkills && jobName && allSkills.length > 0 && availableSkills.length === 0 ? (
        <p className="text-xs text-muted-foreground">No more skills available for this job.</p>
      ) : null}
      <p className="text-[10px] leading-tight text-muted-foreground/70">
        {skills.length}/{maxSkills} slots · order is the priority sent to the simulator · trigger is how often an
        invoked skill fires: High, Normal or Low. Permanently-active skills show {PERMANENTLY_ACTIVE_LABEL} and carry
        the native default level, so the skill and invocation arrays stay aligned.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Monster / pet picker                                                */
/* ------------------------------------------------------------------ */

/** Canonical monster art: the catalog `src` when present, otherwise the wiki icon map. */
export function monsterIcon(entry: { name: string; src?: string }): string | undefined {
  return entry.src || MONSTER_ICON_MAP[entry.name];
}

/** Searchable icon grid over the shared monster catalog - the same picker style the World Builder uses. */
export function MonsterIconPicker({
  open,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (monsterId: number) => void;
}) {
  const [query, setQuery] = useState("");
  const visible = MONSTER_CATALOG.filter((entry) => entry.name.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85dvh] max-w-3xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>Pick a monster</DialogTitle>
        </DialogHeader>
        <label className="flex items-center gap-2 rounded border bg-background px-2 py-1">
          <Search className="h-3.5 w-3.5 text-muted-foreground" />
          <input
            autoFocus
            aria-label="Search monsters"
            placeholder="Find a monster..."
            className="w-full bg-transparent text-sm outline-none"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="grid min-h-0 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-4">
          {visible.map((entry) => (
            <button
              key={entry.id}
              type="button"
              className="rounded-lg border bg-card p-2 hover:border-primary hover:bg-muted"
              onClick={() => {
                onPick(entry.id);
                onOpenChange(false);
                setQuery("");
              }}
              data-monster-option={entry.id}
            >
              <img
                src={monsterIcon(entry)}
                alt=""
                loading="lazy"
                className="h-20 w-full object-contain"
                style={{ imageRendering: "pixelated" }}
              />
              <span className="block text-xs font-medium">{entry.name}</span>
            </button>
          ))}
        </div>
        {visible.length === 0 ? <p className="py-6 text-center text-sm text-muted-foreground">No monsters match your search.</p> : null}
        <p className="text-[10px] text-muted-foreground/70">
          Icons are the original monster body sprites from the shared monster catalog.
        </p>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Pets                                                                */
/* ------------------------------------------------------------------ */

/**
 * One declared household pet: species, name, level, ordered skills and raw parameters. This is the
 * BODY of the pet dialog - the compact {@link PetSlotSquare} only summarises the same pet. Removal is
 * optional here because inside the dialog the footer owns the remove action.
 */
export function PetSlotEditor({
  pet,
  index,
  allSkills,
  onChange,
  onRemove,
}: {
  pet: SavedLoadoutPet;
  index: number;
  allSkills: string[];
  onChange: (next: SavedLoadoutPet) => void;
  onRemove?: () => void;
}) {
  const [showParameters, setShowParameters] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const species = MONSTER_CATALOG.find((entry) => entry.id === pet.monsterId);
  // Canonical first skill of this species: a copied Monster-table field, `null` when that row
  // declares none (-1) and `undefined` when the species has no canonical table row at all.
  const innateSkillId = MONSTER_INNATE_SKILL_BY_ID.get(pet.monsterId);
  const innateEntry = innateSkillId == null ? undefined : SKILL_BY_ID.get(innateSkillId);
  const innateSkillName = innateEntry
    ? renderSkillName(innateEntry)
    : innateSkillId == null
      ? null
      : `Uncatalogued skill ${innateSkillId}`;
  const innateIcon = innateEntry ? getSkillIcon(innateSkillName ?? "") : undefined;
  return (
    <div className="space-y-1.5 rounded-md border border-border/60 bg-muted/15 p-2" data-builder-pet={index}>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          className="flex items-center gap-1.5 rounded border border-input bg-background px-1.5 py-1 text-xs hover:bg-muted"
          onClick={() => setPickerOpen(true)}
          data-action="pick-pet-species"
        >
          {species ? (
            <img
              src={monsterIcon(species)}
              alt=""
              className="h-6 w-6 object-contain"
              style={{ imageRendering: "pixelated" }}
            />
          ) : null}
          {species?.name ?? "Pick monster"}
        </button>
        <Input
          value={pet.name ?? ""}
          onChange={(event) => onChange({ ...pet, name: event.target.value })}
          placeholder={species?.name ?? "Pet name"}
          className="h-7 w-32 text-xs"
          aria-label="Pet name"
        />
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
          Lv
          <NumberField
            value={pet.level ?? 1}
            onChange={(level) => onChange({ ...pet, level })}
            className="h-6 w-12 text-right text-xs tabular-nums"
            ariaLabel="Pet level"
          />
        </label>
        <button
          type="button"
          onClick={() => setShowParameters((value) => !value)}
          className="text-[10px] text-muted-foreground underline hover:text-foreground"
        >
          {showParameters ? "Hide parameters" : "Raw parameters"}
        </button>
        {onRemove ? (
          <button
            type="button"
            onClick={onRemove}
            className="ml-auto rounded p-1 text-muted-foreground hover:text-destructive"
            title="Remove pet"
            aria-label="Remove pet"
            data-action="remove-pet"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
      <MonsterIconPicker open={pickerOpen} onOpenChange={setPickerOpen} onPick={(monsterId) => onChange({ ...pet, monsterId })} />
      {showParameters ? (
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-4">
          {MONSTER_PARAMETER_IDS.map((id) => (
            <label key={id} className="flex items-center justify-between gap-1 text-[10px] text-muted-foreground">
              <span>Param {id}</span>
              <NumberField
                value={pet.parameters?.[String(id)]?.rawValue}
                min={0}
                max={2147483647}
                onChange={(rawValue) =>
                  onChange({ ...pet, parameters: { ...(pet.parameters ?? {}), [String(id)]: { rawValue } } })
                }
                className="h-6 w-16 text-right text-xs tabular-nums"
                ariaLabel={`Pet parameter ${id} raw value`}
              />
            </label>
          ))}
        </div>
      ) : null}
      {/*
        The species' native first skill is the copied Monster-table `skillId` (native
        `CreateMonster 0x147780c` loads it into the initial skill list), so it is shown here and
        exported FIRST, ahead of the player's own declared skills below. It is never filled with, or
        replaced by, a user skill, and it is only left empty when the canonical row declares none or
        the species has no canonical row.
      */}
      <div
        className="flex flex-wrap items-center gap-1.5 rounded-md border border-dashed border-border/70 bg-muted/10 px-1.5 py-1"
        data-pet-innate-slot
        data-pet-innate-skill={
          innateSkillId === undefined ? "" : innateSkillId === null ? "none" : String(innateSkillId)
        }
      >
        <Badge variant="outline" className="shrink-0 text-[9px]">innate</Badge>
        {innateSkillName ? (
          <>
            {innateIcon ? (
              <img src={innateIcon} alt="" className="h-4 w-4 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
            ) : null}
            <span className="text-xs font-medium">{innateSkillName}</span>
            <span className="text-[10px] leading-tight text-muted-foreground">
              Fixed first skill. Add extra skills below.
            </span>
          </>
        ) : innateSkillId === null ? (
          <span className="text-[10px] leading-tight text-muted-foreground">
            This monster has no built-in skill.
          </span>
        ) : (
          <span className="text-[10px] leading-tight text-muted-foreground">
            This species has no canonical Monster table row, so no native first skill is claimed or added.
          </span>
        )}
      </div>
      <SkillSlotEditor
        skills={pet.skills ?? []}
        invocations={(pet.skills ?? []).map((_, i) => pet.skillInvocations?.[i])}
        allSkills={allSkills.filter((name) => name !== innateSkillName)}
        maxSkills={innateSkillId != null ? MAX_SKILL_SLOTS - 1 : MAX_SKILL_SLOTS}
        onChange={(skills, invocations) =>
          onChange({
            ...pet,
            skills,
            skillInvocations: invocations.map((level) => (level === 0 || level === 1 || level === 2 ? level : undefined)) as number[],
          })
        }
        idPrefix={`pet-${index}`}
      />
    </div>
  );
}

/**
 * The building that houses pets: `game-data/facilities.ts` id 157 "Monster Room" - the same id
 * `world-builder.canHousePet` accepts. An empty slot draws that real confirmed facility sprite and
 * only falls back to the shared lucide House + Plus when the sprite is missing.
 */
const MONSTER_ROOM_FACILITY_ID = 157;

/** Compact occupied slot: the species sprite with the declared level underneath. */
function PetSlotSquare({
  pet,
  index,
  over,
  onEdit,
}: {
  pet: SavedLoadoutPet;
  index: number;
  over: boolean;
  onEdit: () => void;
}) {
  const species = MONSTER_CATALOG.find((entry) => entry.id === pet.monsterId);
  const icon = species ? monsterIcon(species) : undefined;
  const level = pet.level ?? 1;
  const label = pet.name?.trim() || species?.name || `Pet ${index + 1}`;
  const accessible = `${label}, level ${level}${over ? ", over the current job pet capacity" : ""}`;
  return (
    <button
      type="button"
      onClick={onEdit}
      aria-label={`Edit ${accessible}`}
      title={`${accessible} - click to edit`}
      className={`flex h-12 w-12 flex-col items-center justify-center gap-0.5 overflow-hidden rounded-md border bg-muted/20 p-0.5 hover:border-primary hover:bg-muted ${
        over ? "border-amber-500/70" : "border-border/70"
      }`}
      data-builder-pet={index}
      data-builder-pet-slot={index}
      data-pet-over-slot={over ? "true" : undefined}
      data-action="edit-pet"
    >
      {icon ? (
        <img src={icon} alt="" className="h-7 w-7 object-contain" style={{ imageRendering: "pixelated" }} />
      ) : (
        <span className="text-[10px] font-semibold text-muted-foreground">?</span>
      )}
      <span className="text-[9px] leading-none tabular-nums text-muted-foreground">Lv {level}</span>
    </button>
  );
}

/** Compact empty slot: the Monster Room sprite (lucide fallback) that opens the species picker. */
function EmptyPetSlot({
  slotIndex,
  slotNumber,
  onPick,
}: {
  slotIndex: number;
  slotNumber: number;
  onPick: () => void;
}) {
  const roomIcon = getFacilityIcon(MONSTER_ROOM_FACILITY_ID);
  const label = `Empty pet slot ${slotNumber}: attach a pet`;
  return (
    <button
      type="button"
      onClick={onPick}
      aria-label={label}
      title={label}
      className="flex h-12 w-12 items-center justify-center rounded-md border border-dashed border-border text-muted-foreground hover:border-primary hover:text-foreground"
      data-empty-pet-slot={slotIndex}
      data-action="add-pet"
    >
      {roomIcon ? (
        <img src={roomIcon} alt="" className="h-7 w-7 object-contain" style={{ imageRendering: "pixelated" }} />
      ) : (
        <span className="flex flex-col items-center">
          <House className="h-4 w-4" />
          <Plus className="h-2.5 w-2.5" />
        </span>
      )}
    </button>
  );
}

/**
 * Local draft dialog for one pet slot (attach or edit).
 *
 * The draft is component state seeded from the clicked pet, so Cancel and Escape (Radix closes the
 * dialog on Escape and on the overlay/close button) discard it without touching the team draft;
 * Confirm is the only write. The capacity is re-read from the CURRENT job/list when the dialog
 * renders, so a job or capacity change while it is open cannot push the list further over the cap -
 * Add stays blocked with the reason instead of committing an extra pet.
 */
function PetEditorDialog({
  mode,
  pet,
  slotNumber,
  allSkills,
  capacity,
  declaredCount,
  jobName,
  onConfirm,
  onCancel,
  onRemove,
}: {
  mode: "add" | "edit";
  pet: SavedLoadoutPet;
  slotNumber: number;
  allSkills: string[];
  capacity: { max: number; source: "user" };
  declaredCount: number;
  jobName: string | undefined;
  onConfirm: (next: SavedLoadoutPet) => void;
  onCancel: () => void;
  onRemove?: () => void;
}) {
  const [draft, setDraft] = useState<SavedLoadoutPet>(pet);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const species = MONSTER_CATALOG.find((entry) => entry.id === draft.monsterId);
  const freeSlots = Math.max(0, capacity.max - declaredCount);

  const confirm = () => {
    if (mode === "add" && freeSlots === 0) {
      setBlocked(
        `${jobName ?? "This job"} has no free pet slot left (${declaredCount}/${capacity.max} declared). Cancel and remove a pet first - the declared list is never trimmed automatically.`,
      );
      return;
    }
    onConfirm(draft);
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <DialogContent className="flex max-h-[85dvh] flex-col overflow-hidden sm:max-w-xl" data-pet-dialog={mode}>
        <DialogHeader>
          <DialogTitle>{mode === "add" ? `Attach pet ${slotNumber}` : `Edit pet ${slotNumber}`}</DialogTitle>
          <DialogDescription>
            {species?.name ?? "Choose a species"}, level and ordered skills - its innate skill is fixed in the first slot.
            Nothing is written to the draft until Confirm; Cancel or Escape leaves the list untouched.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto pr-1">
          <PetSlotEditor pet={draft} index={slotNumber - 1} allSkills={allSkills} onChange={setDraft} />
        </div>
        {blocked ? (
          <p className="text-[11px] leading-tight text-amber-600 dark:text-amber-400" data-pet-dialog-blocked>
            {blocked}
          </p>
        ) : null}
        <DialogFooter className="flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" className="min-h-11 text-xs" onClick={onCancel} data-action="cancel-pet">
            Cancel
          </Button>
          {onRemove ? confirmRemove ? (
            <Button variant="destructive" size="sm" className="min-h-11 text-xs" onClick={onRemove} data-action="confirm-remove-pet">Confirm remove</Button>
          ) : (
            <Button variant="outline" size="sm" className="min-h-11 gap-1 text-xs text-destructive" onClick={() => setConfirmRemove(true)} data-action="remove-pet">
              <Trash2 className="h-3 w-3" /> Remove pet
            </Button>
          ) : null}
          <Button size="sm" className="min-h-11 text-xs" onClick={confirm} data-action="confirm-pet">
            {mode === "add" ? "Attach pet" : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Owner pet slots.
 *
 * Attaching a pet is what declares the resident a house owner: a non-empty list is stored as the
 * resident's `householdPets`, and an empty list stores no declaration at all - there is no separate
 * ownership toggle. Every job shows its compact ~48px slots (user rule: 3 pets for every job, 5 for
 * Rancher): a species sprite with its declared level, then one empty Monster Room slot per free
 * slot. Clicking an empty slot opens the monster picker and then the dialog; clicking a filled slot
 * opens the same dialog, which changes species/level/skills or removes the pet.
 *
 * A list already over the capacity (a saved/imported list or a job change) keeps every slot visible
 * and removable, is reported here, and blocks the run in `battleSetupFromLoadouts`; it is never
 * trimmed.
 */
export function PetSlotsSection({
  character,
  allSkills,
  onPetsChange,
}: {
  character: DraftCharacter;
  allSkills: string[];
  onPetsChange: (pets: SavedLoadoutPet[] | undefined) => void;
}) {
  const capacity = petSlotCapacity(character.jobName);
  const list = character.householdPets ?? [];
  const [addPickerOpen, setAddPickerOpen] = useState(false);
  const [editor, setEditor] = useState<{ mode: "add" | "edit"; index: number; pet: SavedLoadoutPet } | null>(null);
  const emptySlots = Math.max(0, capacity.max - list.length);
  const overAttributed = petCapacityOverBy(character.jobName, list.length);
  const atCapacity = list.length >= capacity.max;

  const commit = (pets: SavedLoadoutPet[]) => onPetsChange(declaredHouseholdPets(pets));
  const openAdd = (speciesId: number) => {
    if (petCapacityOverBy(character.jobName, list.length + 1) > 0) return;
    setEditor({ mode: "add", index: list.length, pet: { monsterId: speciesId } });
  };
  const openEdit = (index: number) => setEditor({ mode: "edit", index, pet: { ...list[index] } });
  const confirmEdit = (next: SavedLoadoutPet) => {
    if (!editor) return;
    if (editor.mode === "add") {
      // Re-check against the current job/list: the capacity can change while the dialog is open.
      if (petCapacityOverBy(character.jobName, list.length + 1) > 0) return;
      commit([...list, next]);
    } else {
      if (editor.index >= list.length) return;
      commit(list.map((entry, i) => (i === editor.index ? next : entry)));
    }
    setEditor(null);
  };
  const removeEdited = () => {
    if (!editor || editor.mode !== "edit") return;
    if (editor.index < list.length) commit(list.filter((_, i) => i !== editor.index));
    setEditor(null);
  };

  return (
    <div className="space-y-1.5" data-builder-pets>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          Household pets
          <span className="ml-1 normal-case font-normal">
            ({list.length}/{capacity.max})
          </span>
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5" data-pet-slot-strip>
        {list.map((pet, index) => (
          <PetSlotSquare
            key={`${pet.monsterId}-${index}`}
            pet={pet}
            index={index}
            over={index >= capacity.max}
            onEdit={() => openEdit(index)}
          />
        ))}
        {Array.from({ length: emptySlots }).map((_, slotIndex) => (
          <EmptyPetSlot
            key={`empty-slot-${slotIndex}`}
            slotIndex={slotIndex}
            slotNumber={list.length + slotIndex + 1}
            onPick={() => setAddPickerOpen(true)}
          />
        ))}
      </div>
      <MonsterIconPicker open={addPickerOpen} onOpenChange={setAddPickerOpen} onPick={openAdd} />

      <p className="text-[10px] leading-tight text-muted-foreground/70">
        {overAttributed > 0
          ? `${character.jobName ?? "This job"}: ${list.length} declared pets in ${capacity.max} slots. Every one stays listed and editable above.`
          : atCapacity
            ? `${character.jobName ?? "This job"}: all ${capacity.max} pet slots used. Click a pet to change its species, level or skills, or to remove it.`
            : `${capacity.max} pet slots. Click an empty room to add a pet.`}
      </p>
      {overAttributed > 0 ? (
        <p className="text-[10px] leading-tight text-amber-600 dark:text-amber-400" data-pet-over-capacity>
          {overAttributed} pet(s) over the {capacity.max}-slot capacity of {character.jobName ?? "this job"}. Every declared
          pet stays listed and removable above; remove {overAttributed} pet(s) or change the job - the run is blocked until
          the list fits, and no pet is dropped automatically.
        </p>
      ) : null}

      {editor ? (
        <PetEditorDialog
          mode={editor.mode}
          pet={editor.pet}
          slotNumber={editor.index + 1}
          allSkills={allSkills}
          capacity={capacity}
          declaredCount={list.length}
          jobName={character.jobName}
          onConfirm={confirmEdit}
          onCancel={() => setEditor(null)}
          onRemove={editor.mode === "edit" ? removeEdited : undefined}
        />
      ) : null}
    </div>
  );
}
