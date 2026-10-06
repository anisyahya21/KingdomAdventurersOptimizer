import { STAT_KEYS, STAT_PARAMETER_IDS, PARAMETER_NATIVE_NAMES, canonicalStatKey } from "@/game-data/stat-parameter-ids";
import { skillActivationStatus } from "@/game-data/skill-job-admission";
import {
  EQUIPMENT_BY_ID,
  equipmentSlotForEntry,
  type EquipmentSlotKey,
  type RawParameterSet,
} from "@/lib/battle-setup";
import {
  jobCurveFor,
  loadoutEquipment,
  loadoutParameters,
  resolveCombatEquipmentId,
  SKILL_ID_BY_NAME,
  statAtLevel,
  type SavedLoadout,
  type SavedLoadoutEquipment,
  type SavedLoadoutPet,
  type SharedLoadoutData,
} from "@/lib/battle-legality";

/**
 * The /battle visual builder's local team draft.
 *
 * The draft is deliberately NOT a new battle model: one draft character IS a `SavedLoadout`, the
 * exact character shape the Loadout Builder stores, so the existing
 * `battleSetupFromLoadouts(...)` conversion (job curve, equipment slot resolution, skill admission
 * and household pets) is reused unchanged. This module only owns the draft container, the ordered
 * team operations and the user's pet-slot capacities (3 for every job, 5 for Rancher).
 */

export const BATTLE_TEAM_DRAFT_KEY = "ka_battle_team_draft";
export const BATTLE_TEAM_DRAFT_SCHEMA = "ka-battle-team-draft-1";

/** One ordered team member: a Loadout-Builder character plus the draft-local identity/declarations. */
export type DraftCharacter = SavedLoadout & { id: string };

export type BattleTeamDraft = {
  schema: typeof BATTLE_TEAM_DRAFT_SCHEMA;
  encounterId: number;
  /** ORDERED roster: index 0 is the first unit sent to the runner. */
  characters: DraftCharacter[];
  /** Extra party slots supplied by the player's own valuable effects, when known. */
  partyBonus?: number;
  /**
   * Provisioned battle consumables: `holyHerbStock` (the built-in herb) plus canonical Item.txt
   * rows with a finite count. This is the stock the replay's consumable clicks spend from, so a
   * run with 0 stock has nothing to click. Optional for older drafts.
   */
  consumables?: DraftConsumables;
};

export type DraftConsumables = {
  holyHerbStock: number;
  /** Canonical Item.txt name -> finite count. */
  itemStock: Record<string, number>;
};

export const DEFAULT_CONSUMABLES: DraftConsumables = { holyHerbStock: 0, itemStock: {} };

/**
 * The declared household list a UI pet list stands for.
 *
 * Attaching a pet is what declares the resident a house owner, so a non-empty list is stored as
 * `householdPets`. An empty list stores `undefined` (no declaration at all) - the editor has no
 * separate ownership toggle. `[]` stays a legal STORED value produced elsewhere (an explicit
 * "owner with no pets"), and the adapter keeps treating it as a declared owner.
 */
export function declaredHouseholdPets(pets: SavedLoadoutPet[]): SavedLoadoutPet[] | undefined {
  return pets.length > 0 ? pets : undefined;
}

/* The per-job pet slot capacity owner is `@/lib/battle-legality` (the conversion enforces it there);
 * these re-exports keep the draft module's existing public surface. */
export {
  DEFAULT_PET_SLOT_CAPACITY,
  PET_SLOT_CAPACITY_BY_JOB,
  PET_SLOT_CAPACITY_NOTE,
  petCapacityOverBy,
  petSlotCapacity,
} from "@/lib/battle-legality";

/** Native skill-slot ceiling the validator enforces for every unit (SkillComponent.ExtendSlot max=9). */
export const MAX_SKILL_SLOTS = 9;

/**
 * The shared `/shared` document the builder reads: the conversion input `SharedLoadoutData` plus the
 * icon tables the Loadout Builder already renders from. No second catalog is built here.
 */
export type BuilderSharedData = SharedLoadoutData & {
  equipIcons?: Record<string, string>;
  statIcons?: Record<string, string>;
};

/** Site equipment names the Loadout Builder offers, filtered to one native slot. */
export function equipmentNamesForSlot(
  data: BuilderSharedData | null,
  slot: EquipmentSlotKey,
): string[] {
  if (!data) return [];
  const names = Object.keys(data.overrides ?? {}).filter((name) => {
    if (!data.slotAssignments?.[name] && !(data.overrides?.[name] && Object.keys(data.overrides[name]).length > 0)) {
      return false;
    }
    return gearSlotForName(name, data.slotAssignments) === slot;
  });
  return names.sort();
}

/**
 * Native invocation index -> the player-facing trigger the in-game screen shows.
 * `GetInvocationRate 0x15dff08` indexes the rate array directly, so 0 is the highest rate.
 */
export const INVOCATION_LEVELS = [
  { value: 0 as const, label: "High" },
  { value: 1 as const, label: "Normal" },
  { value: 2 as const, label: "Low" },
] as const;

export type InvocationLevel = (typeof INVOCATION_LEVELS)[number]["value"];

export function invocationLabel(level: number | undefined): string {
  const entry = INVOCATION_LEVELS.find((option) => option.value === level);
  return entry ? entry.label : "Normal";
}

/** Normalise an unknown stored invocation to the native default 1. */
export function invocationLevelOf(level: number | undefined): InvocationLevel {
  return level === 0 || level === 1 || level === 2 ? level : 1;
}

/** Player-facing label for a skill that is always on and has no trigger level to choose. */
export const PERMANENTLY_ACTIVE_LABEL = "Always active";

export type BuilderSkillTrigger =
  | { status: "active"; label: string; level: InvocationLevel }
  | { status: "permanently_active"; label: string; level: null }
  | { status: "unknown"; label: string; level: null };

/**
 * The builder's per-skill trigger presentation, from the recovered activation rule (`battle-setup`
 * `skillActivationStatus` -> SkillData.flags FLAG_FOR_BATTLE 0x8). An invoked skill shows the
 * High/Normal/Low trigger; a permanently-active row shows "Always active" and no trigger, because
 * the game has no activation requirement to satisfy. The stored invocation level is untouched
 * either way, so `skillInvocations` stays aligned with `skills`.
 */
export function builderSkillTrigger(name: string, level: number | undefined): BuilderSkillTrigger {
  const skillId = SKILL_ID_BY_NAME.get(name);
  const status = skillId === undefined ? "unknown" : skillActivationStatus(skillId);
  if (status === "active") return { status, label: invocationLabel(level), level: invocationLevelOf(level) };
  if (status === "permanently_active") return { status, label: PERMANENTLY_ACTIVE_LABEL, level: null };
  return { status: "unknown", label: "trigger unknown", level: null };
}

/* ------------------------------------------------------------------ */
/* Stat presentation (canonical keys, canonical native names)          */
/* ------------------------------------------------------------------ */

export const BUILDER_STAT_KEYS = STAT_KEYS;

export function statShortLabel(stat: string): string {
  return stat.toUpperCase();
}

export function statFullLabel(stat: string): string {
  const id = STAT_PARAMETER_IDS[stat as (typeof STAT_KEYS)[number]];
  const nativeName = id === undefined ? undefined : PARAMETER_NATIVE_NAMES[id];
  return nativeName && nativeName !== "UNKNOWN" ? nativeName : statShortLabel(stat);
}

/**
 * One displayed stat for the editor: the trained level plus the value the existing conversion
 * produces, so the number in the editor is the number the run uses.
 */
export type DraftStatRow = {
  stat: string;
  level: number;
  /** Job-curve value at the declared level before resident-wide water bonuses. */
  curveValue: number | null;
  /** Resident-wide "Water of ..." contribution applied by the native parameter extra value/maximum. */
  valuableValue: number;
  /** Equipped items at their declared levels with the recovered slot affinity. */
  equipmentValue: number;
  /** curve + valuables + equipment; null when the shared curve has no row for this stat. */
  total: number | null;
};

/**
 * Displayed stats for one character, composed from the SAME owners the conversion uses:
 *
 *  * `loadoutParameters` -> the job curve and native extra value/maximum used by
 *    `battleSetupFromLoadouts`;
 *  * `jobCurveFor` + `statAtLevel` -> the curve part of that value (normalised stat keys, the
 *    canonical formula), so a stat is never reported from a spelling-dependent lookup;
 *  * `loadoutEquipment` -> each equipped slot's recovered affinity;
 *  * the shared `overrides` table, the same base/inc contribution table the Loadout Builder's own
 *    totals use, at the item's declared level.
 *
 * Nothing is invented here: the arithmetic is the native `rawValue + extraValue/extraMax +
 * equipment contribution` composition, with the weak-affinity half applied exactly as the Loadout
 * Builder's display does.
 */
export function draftStatRows(character: SavedLoadout, shared: BuilderSharedData | null): DraftStatRow[] {
  const jobName = character.jobName ?? "";
  const rank = character.rank ?? "";
  const { parameters, levels } = shared
    ? loadoutParameters(character, shared)
    : { parameters: {} as RawParameterSet, levels: {} as Record<string, number> };
  const curve = shared ? jobCurveFor(shared, jobName, rank) : {};
  const slots = shared ? loadoutEquipment(character, shared).slots : null;

  return STAT_KEYS.map((stat) => {
    const level = levels[stat] ?? character.statLevels?.[stat] ?? character.level ?? 1;
    const parameterId = STAT_PARAMETER_IDS[stat];
    const parameter = parameters[String(parameterId)];
    const rawValue = parameter?.rawValue;
    const entry = curve[stat];
    const curveValue = entry ? statAtLevel(entry.base, entry.inc, level) : null;
    const valuableValue = (parameter?.extraValue ?? 0) + (parameter?.extraMax ?? 0);

    let equipmentValue = 0;
    if (shared && slots) {
      for (const slot of BUILDER_EQUIPMENT_SLOTS) {
        const selection = slots[slot.slot];
        if (!selection) continue;
        const name = gearInSlot(character, slot.slot, shared.slotAssignments)?.name;
        if (!name) continue;
        const multiplier = selection.affinity === 0 ? 0.5 : 1;
        for (const [rawKey, contribution] of Object.entries(shared.overrides?.[name] ?? {})) {
          if (canonicalStatKey(rawKey) !== stat) continue;
          const base = contribution?.base ?? 0;
          const inc = contribution?.inc ?? 0;
          if (base || inc) equipmentValue += Math.floor(statAtLevel(base, inc, selection.level) * multiplier);
        }
      }
    }

    const baseValue = rawValue !== undefined ? rawValue : curveValue;
    return {
      stat,
      level,
      curveValue,
      valuableValue,
      equipmentValue,
      total: curveValue === null || baseValue === null ? null : baseValue + valuableValue + equipmentValue,
    };
  });
}

/* ------------------------------------------------------------------ */
/* Gear slot resolution (recovered combat catalog first)               */
/* ------------------------------------------------------------------ */

export const BUILDER_EQUIPMENT_SLOTS: Array<{ slot: EquipmentSlotKey; label: string }> = [
  { slot: "weapon", label: "Weapon" },
  { slot: "shield", label: "Shield" },
  { slot: "head", label: "Head" },
  { slot: "body", label: "Body" },
  { slot: "accessory", label: "Accessory" },
];

/**
 * Which equipment slot a site equipment name belongs to.
 *
 * Resolution order: the recovered combat catalog's own type mapping
 * (`equipmentSlotForEntry`, `EquipComponent.SLOT_*`) and then the site shared `slotAssignments`
 * declaration (where `armor` is the native `body` slot). Returns null when the name is in neither,
 * so the caller can leave the item unassigned instead of guessing.
 */
export function gearSlotForName(
  name: string,
  slotAssignments: Record<string, string> | undefined,
): EquipmentSlotKey | null {
  const combatId = resolveCombatEquipmentId(name);
  if (combatId !== null) {
    const entry = EQUIPMENT_BY_ID.get(combatId);
    if (entry) return equipmentSlotForEntry(entry);
  }
  const declared = slotAssignments?.[name];
  if (declared === "armor") return "body";
  if (declared === "weapon" || declared === "shield" || declared === "head" || declared === "body" || declared === "accessory") {
    return declared;
  }
  return null;
}

/** The gear entry (name + level) currently declared in one slot, or null. */
export function gearInSlot(
  character: SavedLoadout,
  slot: EquipmentSlotKey,
  slotAssignments: Record<string, string> | undefined,
): { name: string; level: number } | null {
  const found = (character.equipment ?? []).find((entry) => gearSlotForName(entry.name, slotAssignments) === slot);
  return found ?? null;
}

/** Replace one slot's gear while keeping every other slot's entry, order and level untouched. */
export function setGearInSlot<T extends SavedLoadout>(
  character: T,
  slot: EquipmentSlotKey,
  next: { name: string; level: number } | null,
  slotAssignments: Record<string, string> | undefined,
): T {
  const kept = (character.equipment ?? []).filter(
    (entry) => gearSlotForName(entry.name, slotAssignments) !== slot,
  );
  return { ...character, equipment: next ? [...kept, next] : kept };
}

export function weaponNameOf(
  character: SavedLoadout,
  slotAssignments: Record<string, string> | undefined,
): string | null {
  return gearInSlot(character, "weapon", slotAssignments)?.name ?? null;
}

export function shieldNameOf(
  character: SavedLoadout,
  slotAssignments: Record<string, string> | undefined,
): string | null {
  return gearInSlot(character, "shield", slotAssignments)?.name ?? null;
}

/* ------------------------------------------------------------------ */
/* Ordered list helpers                                                */
/* ------------------------------------------------------------------ */

export function moveInList<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

/* ------------------------------------------------------------------ */
/* Factories and persistence                                           */
/* ------------------------------------------------------------------ */

function draftId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch {
    /* fall through to the random id below */
  }
  return `draft-${Math.random().toString(36).slice(2, 10)}`;
}

/** A fresh character with the same defaults the battle setup model uses (`createHumanUnit`). */
export function createDraftCharacter(index: number): DraftCharacter {
  return {
    id: draftId(),
    name: `Character ${index}`,
    jobName: "Guard",
    rank: "D",
    gender: 0,
    statLevels: {},
    equipment: [],
    skills: [],
    skillInvocations: [],
  };
}

/* ------------------------------------------------------------------ */
/* Defensive read of the stored `ka_loadouts` list                    */
/* ------------------------------------------------------------------ */

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringField(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function numberArray(value: unknown): number[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((entry): entry is number => typeof entry === "number" && Number.isFinite(entry));
}

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((entry): entry is string => typeof entry === "string");
}

function numberRecord(value: unknown): Record<string, number> | undefined {
  if (!isRecord(value)) return undefined;
  const out: Record<string, number> = {};
  for (const [key, entry] of Object.entries(value)) {
    const count = finiteNumber(entry);
    if (key.trim() && count !== undefined) out[key] = count;
  }
  return out;
}

/**
 * A legacy equipment record (`{ Weapon: { itemName, level } }`, the Equipment Stats page's own
 * saved shape) reads as the declared slot entries; anything else that is not an array is dropped.
 */
function equipmentArray(value: unknown): SavedLoadoutEquipment[] | undefined {
  if (Array.isArray(value)) {
    const out: SavedLoadoutEquipment[] = [];
    for (const entry of value) {
      if (!isRecord(entry)) continue;
      const name = stringField(entry.name) ?? stringField(entry.itemName);
      if (!name?.trim()) continue;
      out.push({ name, level: finiteNumber(entry.level) ?? 1 });
    }
    return out;
  }
  if (isRecord(value)) {
    const out: SavedLoadoutEquipment[] = [];
    for (const entry of Object.values(value)) {
      if (!isRecord(entry)) continue;
      const name = stringField(entry.name) ?? stringField(entry.itemName);
      if (!name?.trim()) continue;
      out.push({ name, level: finiteNumber(entry.level) ?? 1 });
    }
    return out.length > 0 ? out : undefined;
  }
  return undefined;
}

function petArray(value: unknown): SavedLoadoutPet[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const pets: SavedLoadoutPet[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) continue;
    const monsterId = finiteNumber(entry.monsterId);
    if (monsterId === undefined) continue;
    const parameters = isRecord(entry.parameters)
      ? Object.fromEntries(
          Object.entries(entry.parameters)
            .filter(([, parameter]) => isRecord(parameter))
            .map(([key, parameter]) => [key, { ...(parameter as Record<string, number>) }]),
        )
      : undefined;
    pets.push({
      monsterId,
      ...(stringField(entry.name) === undefined ? {} : { name: stringField(entry.name) }),
      ...(finiteNumber(entry.level) === undefined ? {} : { level: finiteNumber(entry.level) }),
      ...(parameters === undefined ? {} : { parameters }),
      ...(stringArray(entry.skills) === undefined ? {} : { skills: stringArray(entry.skills) }),
      ...(numberArray(entry.skillInvocations) === undefined
        ? {}
        : { skillInvocations: numberArray(entry.skillInvocations) }),
    });
  }
  return pets;
}

/**
 * Defensive read of one stored loadout row.
 *
 * The battle pages read the Loadout Builder's own list, but a device list can also hold rows
 * written by an older build or restored from the shared document (a slot-keyed equipment record, a
 * missing per-stat level, a bare `level`). Every field is coerced to the declared `SavedLoadout`
 * shape and a row with no recognisable loadout field at all is dropped, so importing a saved loadout
 * can never throw on stored data and a legacy row still imports instead of blocking the editor.
 * Nothing is invented: an absent field stays absent (the conversion's own declared-input handling
 * decides what that means).
 */
export function normalizeSavedLoadoutRow(raw: unknown): SavedLoadout | null {
  if (!isRecord(raw)) return null;
  const id = stringField(raw.id);
  const name = stringField(raw.name);
  const jobName = stringField(raw.jobName);
  const rank = stringField(raw.rank);
  const level = finiteNumber(raw.level);
  const gender = finiteNumber(raw.gender);
  const awakening = finiteNumber(raw.awakening);
  const statLevels = numberRecord(raw.statLevels);
  const equipment = equipmentArray(raw.equipment);
  const skills = stringArray(raw.skills);
  const skillInvocations = numberArray(raw.skillInvocations);
  const householdPets = petArray(raw.householdPets);
  const residentStatItems = numberRecord(raw.residentStatItems);
  const recognised =
    name !== undefined ||
    jobName !== undefined ||
    rank !== undefined ||
    level !== undefined ||
    statLevels !== undefined ||
    equipment !== undefined ||
    skills !== undefined;
  if (!recognised) return null;
  return {
    ...(id ? { id } : {}),
    ...(name === undefined ? {} : { name }),
    ...(jobName === undefined ? {} : { jobName }),
    ...(rank === undefined ? {} : { rank }),
    ...(level === undefined ? {} : { level }),
    ...(gender === undefined ? {} : { gender }),
    ...(awakening === undefined ? {} : { awakening }),
    statLevels: statLevels ?? {},
    equipment: equipment ?? [],
    skills: skills ?? [],
    ...(skillInvocations === undefined ? {} : { skillInvocations }),
    ...(householdPets === undefined ? {} : { householdPets }),
    ...(residentStatItems === undefined ? {} : { residentStatItems }),
  };
}

/** Defensive read of the whole stored `ka_loadouts` value (a non-array reads as empty). */
export function normalizeSavedLoadouts(raw: unknown): SavedLoadout[] {
  if (!Array.isArray(raw)) return [];
  const rows: SavedLoadout[] = [];
  for (const entry of raw) {
    const row = normalizeSavedLoadoutRow(entry);
    if (row) rows.push(row);
  }
  return rows;
}

/**
 * Copy a saved Loadout-Builder loadout into the draft as a NEW character.
 *
 * Presets are copied, never linked: the new character gets its own id and every nested object is
 * cloned, so edits (and pet/skill lists) can never write back into `ka_loadouts`. The row is read
 * through `normalizeSavedLoadoutRow` first, so a legacy/loose stored row copies instead of
 * throwing on a field the current shape expects to be an array.
 */
export function draftCharacterFromLoadout(loadout: SavedLoadout, index: number): DraftCharacter {
  const source = normalizeSavedLoadoutRow(loadout as unknown) ?? ({} as SavedLoadout);
  return {
    ...source,
    id: draftId(),
    name: source.name?.trim() || source.jobName?.trim() || `Character ${index}`,
    statLevels: { ...(source.statLevels ?? {}) },
    equipment: (source.equipment ?? []).map((entry) => ({ ...entry })),
    skills: [...(source.skills ?? [])],
    skillInvocations: [...(source.skillInvocations ?? [])],
    ...(source.householdPets === undefined
      ? {}
      : {
          householdPets: source.householdPets.map((pet: SavedLoadoutPet) => ({
            ...pet,
            parameters: pet.parameters
              ? Object.fromEntries(Object.entries(pet.parameters).map(([key, value]) => [key, { ...value }]))
              : undefined,
            skills: [...(pet.skills ?? [])],
            skillInvocations: [...(pet.skillInvocations ?? [])],
          })),
        }),
    ...(source.residentStatItems === undefined ? {} : { residentStatItems: { ...source.residentStatItems } }),
  };
}

export function createDraft(encounterId: number): BattleTeamDraft {
  return {
    schema: BATTLE_TEAM_DRAFT_SCHEMA,
    encounterId,
    characters: [],
    consumables: { ...DEFAULT_CONSUMABLES, itemStock: {} },
  };
}

/** Defensive read of the optional consumable provision: non-negative integers only. */
export function normalizeConsumables(raw: unknown): DraftConsumables {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_CONSUMABLES, itemStock: {} };
  const candidate = raw as Partial<DraftConsumables>;
  const holyHerbStock =
    typeof candidate.holyHerbStock === "number" && Number.isInteger(candidate.holyHerbStock) && candidate.holyHerbStock >= 0
      ? candidate.holyHerbStock
      : 0;
  const itemStock: Record<string, number> = {};
  for (const [name, count] of Object.entries(candidate.itemStock ?? {})) {
    if (!name.trim()) continue;
    if (typeof count !== "number" || !Number.isInteger(count) || count < 0) continue;
    itemStock[name] = count;
  }
  return { holyHerbStock, itemStock };
}

/**
 * Read a stored draft defensively: a draft from another schema, or one whose characters are not
 * objects, is rejected (null) rather than half-loaded.
 */
export function normalizeDraft(raw: unknown): BattleTeamDraft | null {
  if (!raw || typeof raw !== "object") return null;
  const candidate = raw as Partial<BattleTeamDraft>;
  if (candidate.schema !== BATTLE_TEAM_DRAFT_SCHEMA) return null;
  if (!Number.isInteger(candidate.encounterId)) return null;
  if (!Array.isArray(candidate.characters)) return null;
  const characters: DraftCharacter[] = [];
  candidate.characters.forEach((entry, index) => {
    if (!entry || typeof entry !== "object") return;
    const character = entry as DraftCharacter;
    if (typeof character.id !== "string" || !character.id) return;
    characters.push({ ...character, name: character.name?.trim() || `Character ${index + 1}` });
  });
  return {
    schema: BATTLE_TEAM_DRAFT_SCHEMA,
    encounterId: candidate.encounterId as number,
    characters,
    ...(typeof candidate.partyBonus === "number" && Number.isInteger(candidate.partyBonus) && candidate.partyBonus >= 0
      ? { partyBonus: candidate.partyBonus }
      : {}),
    consumables: normalizeConsumables(candidate.consumables),
  };
}

/** Skill names offered by the pickers: the site shared skill table, exactly as the Loadout Builder lists them. */
export function builderSkillNames(sharedSkills: Record<string, unknown> | undefined): string[] {
  return Object.keys(sharedSkills ?? {}).sort();
}

export function petLevelTrainingLevel(pet: SavedLoadoutPet): number {
  return typeof pet.level === "number" && Number.isFinite(pet.level) && pet.level >= 1 ? Math.trunc(pet.level) : 1;
}
