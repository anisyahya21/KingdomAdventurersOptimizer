/**
 * Saved-loadout -> BattleSetup legality and transfer.
 *
 * Scope: turn the website's own saved loadout records (the `/loadout` storage format) into a
 * `BattleSetup` for the authoritative runner, check the parts of legality that existing canonical
 * owners can prove, and label every converted field with where it came from.
 *
 * It does not invent combat facts:
 *  * jobs, ranks and job stat curves come from the shared job table (the same owner the loadout
 *    page renders);
 *  * job weapon/shield access comes from `job-profile` (`job-equipment` / `job-skills` owners);
 *  * equipment ids come from the generated site equipment catalog joined to the recovered combat
 *    equipment catalog by id (both tables agree on every shared row);
 *  * skill ids are the recovered skill catalog rows whose `nameText`/`nameArg` template renders to
 *    the site skill name - no heuristic rewriting;
 *  * native parameter ids follow `docs/reverse-engineering/special-combat.md` and
 *    `combat_native.py params`; see `@/game-data/stat-parameter-ids`.
 *
 * Anything the save model does not contain (per-instance equipment affinity, skill invocation
 * levels, gender, appearance, seeds) is reported as `UNKNOWN_NOT_CAPTURED` or `RESEARCH_SYNTHETIC`
 * in the provenance list instead of being presented as a recovered player value.
 */
import { getJobProfile, type SharedJobProfileData } from "@/game-data/job-profile";
import nativeJobIdentities from "@/game-data/native-job-identity.json";
import { RESIDENT_STAT_ITEMS } from "@/game-data/resident-stat-items";
import { residentValuableParameterDeltas } from "@/game-data/resident-valuable-effects";
import {
  EQUIP_AFFINITY_WEAK,
  resolveEquipmentAffinity,
} from "@/game-data/equipment-job-admission";
import {
  CAN_SET_SKILL_DUPLICATE,
  CAN_SET_SKILL_JOB_GROUP_MISMATCH,
  CAN_SET_SKILL_SAME_TYPE,
  canSetSkill,
} from "@/game-data/skill-job-admission";
import { canonicalStatKey, STAT_KEYS, STAT_PARAMETER_IDS, type StatKey } from "@/game-data/stat-parameter-ids";
import { EQUIPMENT_CATALOG as SITE_EQUIPMENT_CATALOG } from "@/lib/generated-equipment-data";
import { JOB_SKILL_DATA } from "@/lib/generated-job-skill-data";
import {
  createDefaultBattleSetup,
  emptyEquipmentSlots,
  emptyParameters,
  EQUIPMENT_BY_ID,
  EQUIPMENT_SLOT_ORDER,
  equipmentSlotForEntry,
  JOB_BY_ID,
  MONSTER_BY_ID,
  MONSTER_INNATE_SKILL_BY_ID,
  MONSTER_PARAMETER_IDS,
  renderSkillName,
  SKILL_CATALOG,
  SKILL_BY_ID,
  validateBattleSetup,
  type BattleSetup,
  type BattleItemRow,
  type EncounterSelection,
  type EquipmentSlotKey,
  type EquipmentSlotSelection,
  type HumanUnit,
  type PetMonsterUnit,
  type RawParameter,
  type RawParameterSet,
  type SetupIssue,
  type SkillSelection,
} from "@/lib/battle-setup";

/* ------------------------------------------------------------------ */
/* Saved loadout / shared data shapes (the existing storage format)     */
/* ------------------------------------------------------------------ */

export type SavedLoadoutEquipment = { name: string; level: number };

/**
 * One household pet attached to a saved loadout (the player's own declared household).
 *
 * `monsterId` is a row of the site monster catalog (`MONSTER_CATALOG`); the converter validates it
 * with the same owner `validateBattleSetup` uses. `level` is a DECLARED training level applied to
 * every monster parameter, `parameters` are optional explicit per-parameter overrides, and `skills`
 * is the ordered list of the player's OWN declared skills (same format as the owner's). The species'
 * canonical first skill is not part of it: `petUnitFromSaved` puts that table value first in the
 * exported skill list. Nothing here invents a stat curve: the
 * monster parameter curve is not recovered, so the level is carried as the declared training level
 * exactly like the human per-stat levels, never expanded into raw stats.
 */
export type SavedLoadoutPet = {
  /** Site monster-catalog id (the supported species list). */
  monsterId: number;
  name?: string;
  /** Declared training level applied to every monster parameter (integer >= 1). */
  level?: number;
  /** Optional explicit per-parameter overrides, keyed by native parameter id. */
  parameters?: Record<
    string,
    { rawValue?: number; rawMax?: number; extraValue?: number; extraMax?: number; trainingLevel?: number }
  >;
  /** Ordered skills (priority order, same as the owner's). */
  skills?: string[];
  /** Declared invocation levels parallel to `skills` (0/1/2), absent entries default to native 1. */
  skillInvocations?: number[];
};

export type SavedLoadout = {
  id?: string;
  name?: string;
  jobName?: string;
  rank?: string;
  /** Legacy fallback level used when a stat has no per-stat level. */
  level?: number;
  /** Per-stat levels, keyed by canonical short stat key. */
  statLevels?: Record<string, number>;
  /**
   * Declared native gender index for this character (0 = male, 1 = female - the same index
   * `HumanUnit.gender` and the shared sprite renderer's `variant - 1` use). The loadout model
   * does not store it, so an absent field keeps the previous conversion behaviour (0) instead of
   * being guessed.
   */
  gender?: number;
  /**
   * Declared native grow/awakening step count for THIS character (the loadout owner's own field;
   * the preset carries `awakening`). It is per unit by construction: the native limit increase is
   * `Job.csv maxLevel + 30 * awakening`, so one unit's steps can never widen another's cap.
   * Undefined means "not declared", not zero.
   */
  awakening?: number;
  equipment?: SavedLoadoutEquipment[];
  /**
   * Ordered skills (priority order as shown in the game). Order is significant: the adapter sends
   * this list verbatim as the unit's `skills` array.
   */
  skills?: string[];
  /**
   * Declared invocation levels parallel to `skills`: native index 0 (highest activation chance),
   * 1 (ordinary, the native default) or 2 (lowest). Absent entries fall back to the caller option,
   * then to the labelled native default 1.
   */
  skillInvocations?: number[];
  /**
   * The owner's COMPLETE ordered household list (native `SearchAllyMonstersInHouse` result order).
   * An absent field means "not declared" (the human is not declared a house owner); an empty array
   * is the explicit "this house owner has no pets" claim. Never inferred from names or positions.
   */
  householdPets?: SavedLoadoutPet[];
  residentStatItems?: Record<string, number | undefined>;
};

export type SharedLoadoutData = SharedJobProfileData & {
  slotAssignments?: Record<string, string>;
  weaponTypes?: Record<string, string>;
  skills?: Record<string, { weaponResistance?: string } | undefined>;
  overrides?: Record<string, Record<string, { base?: number; inc?: number }>>;
};

/* ------------------------------------------------------------------ */
/* Provenance                                                          */
/* ------------------------------------------------------------------ */

export type SetupFieldOrigin =
  /** Copied from the saved loadout. */
  | "PLAYER_LOADOUT"
  /** Derived from canonical site/recovered data (job curve, catalogs, access rules). */
  | "SITE_DERIVED"
  /** Research-only input with no player source. */
  | "RESEARCH_SYNTHETIC"
  /** The player's real value is not in the save model; the value used is a labelled placeholder. */
  | "UNKNOWN_NOT_CAPTURED";

export type SetupProvenanceEntry = {
  field: string;
  origin: SetupFieldOrigin;
  note: string;
};

/* ------------------------------------------------------------------ */
/* Skill name -> recovered skill id                                    */
/* ------------------------------------------------------------------ */

// The catalog owner (`battle-setup.ts`) renders the display name; re-exported here because the
// loadout conversion is the historical import site for callers that only touch this module.
export { renderSkillName };

/** Site skill name -> recovered skill id. Conflicts are listed instead of silently overwritten. */
export const SKILL_ID_BY_NAME = new Map<string, number>();
export const SKILL_NAME_CONFLICTS: Array<{ name: string; ids: number[] }> = [];
for (const entry of SKILL_CATALOG) {
  const name = renderSkillName(entry);
  const existing = SKILL_ID_BY_NAME.get(name);
  if (existing === undefined) SKILL_ID_BY_NAME.set(name, entry.id);
  else {
    const conflict = SKILL_NAME_CONFLICTS.find((item) => item.name === name);
    if (conflict) conflict.ids.push(entry.id);
    else SKILL_NAME_CONFLICTS.push({ name, ids: [existing, entry.id] });
  }
}

export function battleSkillIdForName(name: string): number | null {
  return SKILL_ID_BY_NAME.get(name) ?? null;
}

/* ------------------------------------------------------------------ */
/* Equipment name -> recovered equipment id                            */
/* ------------------------------------------------------------------ */

/** Generated site equipment catalog name -> id (the same ids the recovered combat catalog uses). */
export const SITE_EQUIPMENT_ID_BY_NAME = new Map<string, number>(
  SITE_EQUIPMENT_CATALOG.map((entry) => [entry.name, entry.id]),
);

const COMBAT_EQUIPMENT_ID_BY_NAME = new Map<string, number>(
  Array.from(EQUIPMENT_BY_ID.values()).map((entry) => [entry.name, entry.id]),
);

export function resolveCombatEquipmentId(name: string): number | null {
  const siteId = SITE_EQUIPMENT_ID_BY_NAME.get(name);
  if (siteId !== undefined && EQUIPMENT_BY_ID.has(siteId)) return siteId;
  return COMBAT_EQUIPMENT_ID_BY_NAME.get(name) ?? null;
}

/**
 * Site rows whose contribution numbers disagree with the recovered row of the same id.
 *
 * Two generated site rows (`E/ Hat (B)` and `B/ Legendary Shield (B)`) share a display name with
 * another recovered row and carry that other row's contribution pairs, so the name alone cannot
 * pick between the two recovered ids; their `(R)` siblings do match their own recovered ids.
 * The transfer keeps the id join and reports the ambiguity instead of hiding it.
 */
export function equipmentVariantAmbiguity(
  name: string,
  entry: { id: number; name: string; parameters: number[][] },
  overrides: SharedLoadoutData["overrides"],
): number[] {
  const siteStats = overrides?.[name];
  if (!siteStats) return [];
  const matches = Object.entries(siteStats).every(([rawStat, pair]) => {
    const parameterId = STAT_PARAMETER_IDS[canonicalStatKey(rawStat) as StatKey];
    if (!parameterId) return false;
    const native = entry.parameters[parameterId - 10] ?? [0, 0];
    return (pair?.base ?? 0) === native[0] && (pair?.inc ?? 0) === native[1];
  });
  if (matches) return [];
  return Array.from(EQUIPMENT_BY_ID.values())
    .filter((candidate) => candidate.id !== entry.id && candidate.name === entry.name)
    .map((candidate) => candidate.id);
}

const SLOT_KEY_BY_SITE_SLOT: Record<string, EquipmentSlotKey> = {
  weapon: "weapon",
  shield: "shield",
  head: "head",
  armor: "body",
  body: "body",
  accessory: "accessory",
};

/* ------------------------------------------------------------------ */
/* Issue helpers                                                       */
/* ------------------------------------------------------------------ */

function error(code: string, path: string, message: string): SetupIssue {
  return { category: "ERROR", code, path, message };
}

function warning(code: string, path: string, message: string): SetupIssue {
  return { category: "WARNING", code, path, message };
}

function unknown(code: string, path: string, message: string): SetupIssue {
  return { category: "UNKNOWN_NATIVE_RULE", code, path, message };
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function isInt32(value: number): boolean {
  return Number.isInteger(value) && value >= -2147483648 && value <= 2147483647;
}

/**
 * The native `rawMax` sentinel for an uncapped parameter.
 *
 * `combat_parameters.fighter_parameter` (differential-tested against the emulated native
 * `Original Param.GetValue/GetMaxValue` getters by `tools/recovery/check_combat_parameters.py`)
 * adds the equipment contribution while the parameter is *unbounded*, i.e. while
 * `rawMax == 2147483647`, and for a bounded (`rawMax != 2147483647`) parameter only the maximum
 * receives equipment. The recovered research fixtures (`check_combat_sandbox.py`,
 * `check_combat_shared_controllers.py`) therefore write the real cap for the bounded parameters
 * (`BOUNDED_PARAMETER_IDS`) and this sentinel for every other stat. Copying `rawValue` into
 * `rawMax` instead made every stat bounded and silently suppressed the whole equipment
 * contribution from the effective value.
 */
export const UNBOUNDED_PARAMETER_MAX = 2147483647;

/**
 * Native ids of the bounded (capped) parameters the loadout stat keys reach.
 *
 * `JobData.ParamHasMaxValue` is the bitmask `0x8007` over `(paramId - 10)`, i.e. ids 10/11/12/25
 * (HP, MP, Vigor/"Energy", Life). `Param.CreateAdventurerParamSet` writes `maxValue = value` for
 * the bounded ids 10/11/12 and INT_MAX otherwise, and `ExpSystem.LevelUp` raises bounded
 * parameters through `Parameter.AddMaxValue` (explicitly "bounded HP/MP/Vigor") while unbounded
 * ones go through `Param.AddValue` -> `Parameter.Add(value, INT_MAX)`. The native fixture
 * `tools/recovery/check_combat_job_parameters.py` asserts this (`BOUNDED_PARAMS = (10, 11, 12, 25)`;
 * see the findings array), and `docs/reverse-engineering/special-combat.md` states the consequence:
 * "Equipment increases bounded maximums and unbounded currents. Effective bounded current does not
 * automatically gain the equipment bonus; the already recovered special-fight refill is a separate
 * write." The special-fight launcher refills only HP(10) and MP(11); Vigor(12) is bounded but is
 * not refilled, so writing it unbounded would wrongly add equipment to the effective Vigor value.
 * Life(25) is never a loadout stat key, so only 10/11/12 are reachable here.
 */
const BOUNDED_PARAMETER_IDS = new Set([10, 11, 12]);

/** Read-only view of the generated job-skill table (job -> rank -> learned skill names). */
const JOB_SKILL_TABLE: Record<string, Record<string, readonly string[]>> = JOB_SKILL_DATA;

/* ------------------------------------------------------------------ */
/* Canonical Job.csv combat job identity                               */
/* ------------------------------------------------------------------ */

export type NativeJobRow = {
  appName: string;
  sheetName: string;
  csvId: number;
  sheetRankToken: string;
  lifeColumn12: number | null;
  parameters: number[][];
};

type NativeJobIdentityFile = {
  source: { table: string; route: string; nameRule: string; sha256: string };
  nativeCrossCheck: { tool: string; note: string; boundedParams: number[] };
  jobRows: NativeJobRow[];
};

/**
 * Combat job identities recovered from the original `KA GameData - Job.csv` (the producer row).
 *
 * `data/sheet-research/notes/job-notes.md` confirms the `name` rule: `"<token> Rank <job>"` is
 * combat and `"<token> Grade <job>"` is non-combat. The generated catalog `JOB_SKILL_DATA` only
 * has the D/C/B/A/S rank keys, so a genuine `Rank` row with any other token - the single
 * `F Rank Scholar` (csvId 132) - is absent from `JOB_CATALOG` while the shared job table still
 * carries its curve. Reading this canonical source lets the converter resolve such a job to its
 * real row (name + id + rank token) instead of blocking it or silently substituting another job.
 */
export const NATIVE_JOB_IDENTITY = nativeJobIdentities as unknown as NativeJobIdentityFile;

/** Job.csv combat rows grouped by their app-facing job name, in sheet order. */
export const COMBAT_JOB_ROWS_BY_NAME = new Map<string, NativeJobRow[]>();
for (const row of NATIVE_JOB_IDENTITY.jobRows) {
  const rows = COMBAT_JOB_ROWS_BY_NAME.get(row.appName);
  if (rows) rows.push(row);
  else COMBAT_JOB_ROWS_BY_NAME.set(row.appName, [row]);
}

function findJobSkillRanks(jobName: string, skillName: string): string[] {
  const ranks = JOB_SKILL_TABLE[jobName];
  if (!ranks) return [];
  return Object.keys(ranks).filter((rank) => (ranks[rank] ?? []).includes(skillName));
}

/* ------------------------------------------------------------------ */
/* Job stat curve -> native raw parameters                             */
/* ------------------------------------------------------------------ */

/** Per-stat level actually used for a stat (per-stat level, else legacy level, else 1). */
export function loadoutStatLevel(loadout: SavedLoadout, stat: StatKey): number {
  const raw = loadout.statLevels?.[stat] ?? loadout.level ?? 1;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 1;
}

/**
 * Shared job curve for one job/rank, keyed by the canonical stat key.
 *
 * The shared table stores its own spellings ("Vigor", "Defence", "Gather", ...); every key is
 * normalised here with the same `canonicalStatKey` alias map the conversion uses, so a consumer
 * (the builder's stat editor included) indexes the curve with canonical keys instead of
 * re-deriving the spelling map. Exported so the editor reuses this instead of a parallel lookup.
 */
export function jobCurveFor(shared: SharedLoadoutData, jobName: string, rank: string): Record<string, { base: number; inc: number }> {
  const ranks = shared.jobs?.[jobName]?.ranks as Record<string, { stats?: Record<string, { base: number; inc: number }> }> | undefined;
  const stats = ranks?.[rank]?.stats ?? {};
  const curve: Record<string, { base: number; inc: number }> = {};
  for (const [rawName, entry] of Object.entries(stats)) {
    curve[canonicalStatKey(rawName)] = { base: entry?.base ?? 0, inc: entry?.inc ?? 0 };
  }
  return curve;
}

/** The canonical curve formula (`base + (level - 1) * inc`, rounded) used by every stat total. */
export function statAtLevel(base: number, inc: number, level: number): number {
  return Math.round(base + (level - 1) * inc);
}

/**
 * Convert the saved per-stat levels plus the job curve plus the resident-wide valuables into the
 * native raw/extra parameter shape.
 *
 * The recovered composition is `effective = rawValue + extraValue + equipment contributions`
 * (`combat_setup.fighter_parameter`), and the site total is `jobCurve + valuables + equipment`,
 * so the loadout's non-equipment part is carried as `rawValue` (job curve at the saved level) plus
 * the valuables in the slot native writes them to: `resident-valuable-effects.ts` recovers that
 * HP(10)/MP(11)/Vigor(12) raise the parameter's *maximum* (`extraMax`) while Attack(13)/
 * Defence(14) raise its *value* (`extraValue`). The native raw/training split itself is not stored
 * in the save.
 *
 * `residentStatItems` is declared input: an explicit empty record means "declared none", while an
 * absent field reports RESIDENT_VALUABLES_NOT_CAPTURED rather than being read as an owned zero.
 * A present record may still have individual counts that were not captured; those get item-level
 * unknown-rule issues while known item counts continue to contribute normally.
 */
export function loadoutParameters(
  loadout: SavedLoadout,
  shared: SharedLoadoutData,
  options: LoadoutConversionOptions = {},
): { parameters: RawParameterSet; levels: Record<string, number>; issues: SetupIssue[] } {
  const issues: SetupIssue[] = [];
  const jobName = loadout.jobName ?? "";
  const rank = loadout.rank ?? "";
  const curve = jobCurveFor(shared, jobName, rank);
  // The save model does not carry the "Water of ..." ownership; an absent field is "not captured",
  // not "owns none" (an explicit empty record is the declared-none form).
  if (loadout.residentStatItems === undefined) {
    issues.push(
      unknown(
        "RESIDENT_VALUABLES_NOT_CAPTURED",
        `${jobName}.residentStatItems`,
        "Resident-wide 'Water of ...' ownership is not stored in the saved loadout; no bonus was assumed (declared-input zero). A loadout that has captured the counts should supply residentStatItems.",
      ),
    );
  } else if (Object.keys(loadout.residentStatItems).length > 0) {
    for (const item of RESIDENT_STAT_ITEMS) {
      if (loadout.residentStatItems[item.key] !== undefined) continue;
      issues.push(
        unknown(
          "RESIDENT_VALUABLE_ITEM_NOT_CAPTURED",
          `${jobName}.residentStatItems.${item.key}`,
          `${item.name} ownership is not captured; its declared-input bonus is zero until a count is supplied.`,
        ),
      );
    }
  }
  const valuableDeltas = residentValuableParameterDeltas(loadout.residentStatItems);
  const parameters: RawParameterSet = {};
  const levels: Record<string, number> = {};

  for (const stat of STAT_KEYS) {
    const level = loadoutStatLevel(loadout, stat);
    const parameterId = STAT_PARAMETER_IDS[stat];
    if (!isInteger(level) || level < 1) {
      issues.push(
        error(
          "LOADOUT_STAT_LEVEL_INVALID",
          `${jobName}.statLevels.${stat}`,
          `Saved level ${JSON.stringify(level)} for stat '${stat}' must be an integer >= 1.`,
        ),
      );
    }
    const safeLevel = isInteger(level) && level >= 1 ? level : 1;
    levels[stat] = safeLevel;
    const entry = curve[stat];
    if (!entry) {
      issues.push(
        unknown(
          "LOADOUT_JOB_CURVE_STAT_MISSING",
          `${jobName}.${rank}.${stat}`,
          `The shared job curve for '${jobName}' rank ${rank} has no '${stat}' entry; rawValue 0 was used for parameter ${parameterId}.`,
        ),
      );
    }
    const rawValue = entry ? statAtLevel(entry.base, entry.inc, safeLevel) : 0;
    const valuableDelta = valuableDeltas[parameterId] ?? { valueDelta: 0, maxDelta: 0 };
    const extraValue = valuableDelta.valueDelta;
    const extraMax = valuableDelta.maxDelta;
    if (!isInt32(rawValue) || !isInt32(extraValue) || !isInt32(extraMax)) {
      issues.push(
        error(
          "PARAMETER_OUT_OF_SIGNED32_RANGE",
          `${jobName}.parameters.${parameterId}`,
          `Computed ${stat} value ${rawValue} with extra value ${extraValue} / extra max ${extraMax} does not fit signed 32-bit storage.`,
        ),
      );
    }
    parameters[String(parameterId)] = {
      rawValue,
      // HP/MP/Vigor keep their real cap: HP/MP because the special launcher refills them to the
      // effective maximum, Vigor because native keeps it bounded (bounded = rawMax != sentinel, the
      // state in which the recovered getter adds equipment to the maximum but never to the value).
      // Every other stat is uncapped, the only state in which the getter adds equipment to the value.
      rawMax: BOUNDED_PARAMETER_IDS.has(parameterId) ? rawValue : UNBOUNDED_PARAMETER_MAX,
      extraValue,
      extraMax,
      trainingLevel: safeLevel,
    };
  }
  return { parameters, levels, issues };
}

/* ------------------------------------------------------------------ */
/* Equipment                                                           */
/* ------------------------------------------------------------------ */

export type LoadoutEquipmentResult = {
  slots: Record<EquipmentSlotKey, EquipmentSlotSelection | null>;
  weaponId: number;
  issues: SetupIssue[];
};

export function loadoutEquipment(
  loadout: SavedLoadout,
  shared: SharedLoadoutData,
): LoadoutEquipmentResult {
  const issues: SetupIssue[] = [];
  const slots = emptyEquipmentSlots();
  const slotNames: Partial<Record<EquipmentSlotKey, string>> = {};
  const jobName = loadout.jobName ?? "";
  const profile = jobName ? getJobProfile(shared, jobName) : null;
  const equippedSkills = (loadout.skills ?? []).filter((name): name is string => typeof name === "string");
  const unruledSlots = new Set<"head" | "body" | "accessory">();
  let sawUnknownWeaponAccess = false;
  let sawHalvingAssumption = false;

  (loadout.equipment ?? []).forEach((entry, index) => {
    const path = `${jobName}.equipment[${index}]`;
    const name = typeof entry?.name === "string" ? entry.name : "";
    if (!name) {
      issues.push(error("EQUIPMENT_NAME_UNRESOLVED", path, "Saved equipment entry has no name."));
      return;
    }
    const id = resolveCombatEquipmentId(name);
    if (id === null) {
      issues.push(
        error(
          "EQUIPMENT_NAME_UNRESOLVED",
          path,
          `Equipment '${name}' is not in the generated site equipment catalog or the recovered combat equipment catalog; it was not dropped silently and the setup is blocked.`,
        ),
      );
      return;
    }
    const catalogEntry = EQUIPMENT_BY_ID.get(id);
    if (!catalogEntry) {
      issues.push(
        error(
          "EQUIPMENT_ID_NOT_IN_COMBAT_CATALOG",
          path,
          `Equipment '${name}' resolves to id ${id}, which is not in the recovered combat equipment catalog.`,
        ),
      );
      return;
    }
    const ambiguousIds = equipmentVariantAmbiguity(name, catalogEntry, shared.overrides);
    if (ambiguousIds.length > 0) {
      issues.push(
        unknown(
          "EQUIPMENT_VARIANT_ID_AMBIGUOUS",
          path,
          `Equipment '${name}' shares the recovered display name '${catalogEntry.name}' with id ${ambiguousIds.join("/")}, and the site contribution table for this name matches that other row rather than id ${catalogEntry.id}. The id ${catalogEntry.id} join was kept and the native contribution difference is not recoverable from the site table.`,
        ),
      );
    }
    const derivedSlot = equipmentSlotForEntry(catalogEntry);
    const siteSlotRaw = shared.slotAssignments?.[name];
    const siteSlot = siteSlotRaw ? SLOT_KEY_BY_SITE_SLOT[siteSlotRaw.toLowerCase()] : undefined;
    if (siteSlot && siteSlot !== derivedSlot) {
      issues.push(
        error(
          "EQUIPMENT_SLOT_CONFLICT",
          path,
          `Equipment '${name}' is assigned to slot '${siteSlotRaw}' by the site table but the recovered catalog type ${catalogEntry.type} is the '${derivedSlot}' slot.`,
        ),
      );
    }
    const slot = derivedSlot;
    if (slots[slot]) {
      issues.push(
        error(
          "EQUIPMENT_SLOT_DUPLICATE",
          path,
          `Equipment '${name}' and '${slotNames[slot] ?? "?"}' both map to the '${slot}' slot; the native EquipComponent has one record per slot.`,
        ),
      );
      return;
    }
    const rawLevel = entry?.level;
    if (!isInteger(rawLevel) || rawLevel < 1) {
      issues.push(
        error(
          "EQUIPMENT_LEVEL_INVALID",
          `${path}.level`,
          `Saved level ${JSON.stringify(rawLevel)} for '${name}' must be an integer >= 1.`,
        ),
      );
    }
    const level = isInteger(rawLevel) && rawLevel >= 1 ? rawLevel : 1;

    let affinity = 1;
    if (slot === "weapon" || slot === "shield") {
      const weaponType = slot === "shield" ? "Shield" : shared.weaponTypes?.[name] ?? null;
      const access = slot === "shield"
        ? profile?.equipmentAccess.shield ?? null
        : weaponType && weaponType !== "Tool"
          ? profile?.equipmentAccess.weapons[weaponType] ?? null
          : null;
      // The native rule owns the verdict: `JobData.GetAffinity 0x16208d0` maps the declared job
      // access onto affinity -1 / 0 / 1, then lifts a -1 or 0 to 1 when the character POSSESSES a
      // type-48 EQUIP_MASTER skill whose `value` equals the equipment type; `EquipData.CanEquip
      // 0x1620e14` rejects exactly the remaining -1. The declared skill names are resolved to
      // catalog ids here, and the catalog ids are never re-derived from the resistance display name.
      const possessedSkillIds = equippedSkills
        .map((skillName) => battleSkillIdForName(skillName))
        .filter((skillId): skillId is number => skillId !== null);
      const verdict = resolveEquipmentAffinity({
        access,
        equipmentType: catalogEntry.type,
        possessedSkillIds,
      });
      affinity = verdict.affinity;
      const resistanceSkillName =
        verdict.equipMasterSkillId === null
          ? null
          : renderSkillName(
              SKILL_BY_ID.get(verdict.equipMasterSkillId) ?? { nameText: `skill ${verdict.equipMasterSkillId}`, nameArg: "" },
            );
      if (!verdict.allowed) {
        issues.push(
          error(
            "JOB_EQUIPMENT_NOT_ALLOWED",
            path,
            `Job '${jobName}' cannot equip ${weaponType ?? "this item"}: native affinity ${verdict.declaredAffinity} (${access}) and no possessed type-48 EQUIP_MASTER skill matches equipment type ${catalogEntry.type}. EquipData.CanEquip 0x1620e14 rejects exactly affinity -1, so '${name}' was not sent to the runner.`,
          ),
        );
      } else if (verdict.overridden) {
        issues.push(
          unknown(
            "RESISTANCE_SKILL_EQUIP_OVERRIDDEN",
            path,
            `Job '${jobName}' has native affinity ${verdict.declaredAffinity} for ${weaponType ?? "this item"} (${access}), and the possessed type-48 EQUIP_MASTER skill '${resistanceSkillName}' has value ${catalogEntry.type} - the recovered override in JobData.GetAffinity 0x16208d0 sets affinity 1, so the item is allowed and no halving applies. Whether the character legitimately owns that skill stays a declared input, not inferred from the skill name.`,
          ),
        );
      } else if (verdict.declaredAffinity === EQUIP_AFFINITY_WEAK) {
        if (!sawHalvingAssumption) {
          sawHalvingAssumption = true;
          issues.push(
            unknown(
              "AFFINITY_HALVING_FROM_WEAKNESS",
              path,
              `Job '${jobName}' is weak with ${weaponType ?? "this weapon type"}; native affinity 0 means half - max(1, trunc(contribution/2)) (affinity_contribution, combat_resolution.py). Per-instance affinity is not stored in the save, so encoding the weak weapon as 0 stays a declared input.`,
            ),
          );
        }
      } else if (access === null || access === undefined) {
        sawUnknownWeaponAccess = sawUnknownWeaponAccess || weaponType === "Tool" || weaponType === null;
      }
    } else {
      if (slot === "head" || slot === "body" || slot === "accessory") unruledSlots.add(slot);
    }

    slots[slot] = { id, level, affinity };
    slotNames[slot] = name;
  });

  const unresolvedSlotRules: Array<{ slot: "head" | "body" | "accessory"; equipmentType: number; code: string }> = [
    { slot: "head", equipmentType: 13, code: "HEAD_EQUIPMENT_ACCESS_UNKNOWN" },
    { slot: "body", equipmentType: 12, code: "BODY_EQUIPMENT_ACCESS_UNKNOWN" },
    { slot: "accessory", equipmentType: 14, code: "ACCESSORY_EQUIPMENT_ACCESS_UNKNOWN" },
  ];
  for (const rule of unresolvedSlotRules) {
    if (!unruledSlots.has(rule.slot)) continue;
    issues.push(unknown(
      rule.code,
      `${jobName}.equipment.${rule.slot}`,
      `The Loadout Builder currently admits ${rule.slot} equipment without a job filter, but that UI behavior does not establish a game rule. Native JobData.GetAffinity 0x16208d0 indexes job-group affinity by equipment type ${rule.equipmentType}; the canonical site access table only names weapon classes and shields, so this slot's job affinity remains unresolved. The selected item is transferred for runtime preview without an admission claim.`,
    ));
  }
  if (sawUnknownWeaponAccess) {
    issues.push(
      unknown(
        "JOB_WEAPON_ACCESS_UNKNOWN",
        `${jobName}.equipment`,
        "No recovered job weapon-access row covers one of the selected weapon types (for example a Tool); the item was transferred without an access verdict.",
      ),
    );
  }

  const weaponId = slots.weapon?.id ?? 0;
  return { slots, weaponId, issues };
}

/* ------------------------------------------------------------------ */
/* Skills                                                              */
/* ------------------------------------------------------------------ */

export type LoadoutSkillsResult = {
  skills: SkillSelection[];
  issues: SetupIssue[];
};

export function loadoutSkills(
  loadout: SavedLoadout,
  options: LoadoutConversionOptions = {},
): LoadoutSkillsResult {
  const issues: SetupIssue[] = [];
  const skills: SkillSelection[] = [];
  const jobName = loadout.jobName ?? "";
  const rank = loadout.rank ?? "";
  const seen = new Set<number>();
  let sawDefaultedInvocation = false;

  (loadout.skills ?? []).forEach((rawName, index) => {
    const path = `${jobName}.skills[${index}]`;
    const name = typeof rawName === "string" ? rawName : "";
    if (!name) {
      issues.push(error("SKILL_NAME_UNRESOLVED", path, "Saved skill entry is empty."));
      return;
    }
    const skillId = battleSkillIdForName(name);
    if (skillId === null) {
      issues.push(
        error(
          "SKILL_NAME_UNRESOLVED",
          path,
          `Skill '${name}' has no recovered skill catalog row whose name template renders to it; it was not dropped silently and the setup is blocked.`,
        ),
      );
      return;
    }
    // Resolve the native admission gate from master data (SkillComponent.CanSetSkill 0x14cfe40:
    // SkillData +0x1c flag vs JobGroupData +0x1c mask). This is a master-data fact, not the saved
    // loadout, so the "bitmasks absent from the save" unknown no longer applies.
    const admission = canSetSkill({ jobName, skillId, existingSkillIds: [...seen] });
    if (admission.status === "rejected") {
      const code =
        admission.failure === CAN_SET_SKILL_JOB_GROUP_MISMATCH
          ? "SKILL_JOB_GROUP_REJECTED"
          : admission.failure === CAN_SET_SKILL_DUPLICATE
            ? "SKILL_DUPLICATE_ID"
            : "SKILL_SAME_TYPE";
      issues.push(
        error(
          code,
          path,
          `${admission.reason} Recovered master-data gate (Job.csv 'group' -> JobGroup.csv flag vs SkillData +0x1c flag), not saved-loadout ownership.`,
        ),
      );
    } else if (admission.status === "unknown") {
      issues.push(unknown("SKILL_JOB_GROUP_UNKNOWN", path, admission.reason));
    }
    seen.add(skillId);

    const ownRanks = findJobSkillRanks(jobName, name);
    if (ownRanks.length > 0) {
      if (!ownRanks.includes(rank)) {
        issues.push(
          unknown(
            "SKILL_RANK_MISMATCH",
            path,
            `Skill '${name}' is learned at rank ${ownRanks.join("/")} of '${jobName}', not at the saved rank ${rank}. CanSetSkill 0x14cfe40 has no rank condition (only the job-group bitmask, duplicate and same-type checks), so rank persistence is not gated at admission and stays a declared input.`,
          ),
        );
      }
    }

    // The loadout's own declared level wins over a caller default; both are optional and an absent
    // value still means the labelled native default 1, never a guess.
    const declared = loadout.skillInvocations?.[index];
    const supplied =
      declared === 0 || declared === 1 || declared === 2
        ? declared
        : options.invocationLevelFor?.(name, index);
    // Native default is 1, not 0: Entity.AddSkill 0x1472564 initialises invocation levels
    // to 1, and GetInvocationRate 0x15dff08 indexes its rate array directly (level 1 = the
    // 18% ordinary default; 0 = 36% highest, 2 = 9% lowest).
    const invocationLevel = supplied === 0 || supplied === 1 || supplied === 2 ? supplied : 1;
    if (supplied === undefined) sawDefaultedInvocation = true;
    skills.push({ skillId, invocationLevel });
  });

  if (sawDefaultedInvocation) {
    issues.push(
      unknown(
        "INVOCATION_LEVEL_NOT_CAPTURED",
        `${jobName}.skills`,
        "Some skill slots have no declared invocation level (the save model does not carry one); the native default level 1 (18% ordinary per GetInvocationRate 0x15dff08) was used for those slots instead of guessing. Declared levels are stored on the loadout's skillInvocations array.",
      ),
    );
  }
  return { skills, issues };
}

/* ------------------------------------------------------------------ */
/* Human unit / team conversion                                        */
/* ------------------------------------------------------------------ */

export type LoadoutConversionOptions = {
  encounterId?: number;
  seeds?: { mathSeed: number; libSeed: number };
  tickLimit?: number;
  holyHerbStock?: number;
  /** Optional explicit recovered battle-item rows, keyed by canonical Item.txt name. */
  items?: Record<string, BattleItemRow>;
  /** Finite per-item stock the replay's consumable clicks spend from. */
  itemStock?: Record<string, number>;
  defeatCount?: number;
  /** Explicit party cap from valuable ownership; left undefined means "not captured". */
  partyMax?: number;
  invocationLevelFor?: (skillName: string, index: number) => 0 | 1 | 2;
};

export type HumanUnitConversion = {
  unit: HumanUnit;
  issues: SetupIssue[];
};

function encounterSelection(encounterId: number): EncounterSelection {
  const base = createDefaultBattleSetup(encounterId);
  return base.encounter;
}

/** Convert one saved loadout into a human unit. Errors block the setup but never drop data silently. */
export function humanUnitFromLoadout(
  loadout: SavedLoadout,
  shared: SharedLoadoutData,
  options: LoadoutConversionOptions = {},
  unitName?: string,
): HumanUnitConversion {
  const issues: SetupIssue[] = [];
  const jobName = loadout.jobName ?? "";
  const rank = loadout.rank ?? "";

  const sharedJob = shared.jobs?.[jobName];
  if (!jobName) {
    issues.push(error("LOADOUT_JOB_UNKNOWN", "jobName", "The saved loadout has no job name."));
  } else if (!sharedJob) {
    issues.push(
      error(
        "LOADOUT_JOB_UNKNOWN",
        "jobName",
        `Job '${jobName}' is not in the shared job table; its stats cannot be converted.`,
      ),
    );
  } else if (!sharedJob.ranks?.[rank]) {
    issues.push(
      error(
        "LOADOUT_RANK_UNKNOWN",
        "rank",
        `Saved rank ${JSON.stringify(rank)} is not present for job '${jobName}'.`,
      ),
    );
  }
  const canonicalJobRows = jobName ? COMBAT_JOB_ROWS_BY_NAME.get(jobName) : undefined;
  if (jobName && !JOB_BY_ID.has(jobName) && !canonicalJobRows) {
    issues.push(
      error(
        "JOB_NOT_IN_COMBAT_CATALOG",
        "jobName",
        `Job '${jobName}' is missing from the Job.csv-derived combat job catalog used by the battle setup model; combat legality for this job is not recovered, so the setup is blocked rather than guessed.`,
      ),
    );
  } else if (jobName && !JOB_BY_ID.has(jobName) && canonicalJobRows) {
    const sheetNames = canonicalJobRows.map((row) => `${row.sheetName} (csvId ${row.csvId})`).join(", ");
    const sheetTokens = canonicalJobRows.map((row) => `"${row.sheetRankToken}"`).join("/");
    issues.push(
      unknown(
        "JOB_CATALOG_IDENTITY_ALIAS",
        "jobName",
        `Job "${jobName}" is absent from the generated Job.csv rank catalog (keys D/C/B/A/S only) but resolves through the canonical Job.csv producer (native-job-identity.json) as ${sheetNames} with sheet rank token(s) ${sheetTokens}. battle-setup.ts admits it by that canonical identity rather than substituting another job; the app-facing saved rank key is carried onto the unit unchanged and the sheet token is provenance, not a second rank.`,
      ),
    );
  }

  const { parameters, issues: parameterIssues } = loadoutParameters(loadout, shared, options);
  const equipment = loadoutEquipment(loadout, shared);
  const skillResult = loadoutSkills(loadout, options);
  issues.push(...parameterIssues, ...equipment.issues, ...skillResult.issues);

  const fallbackName = jobName || `Unit`;
  const unit: HumanUnit = {
    kind: "human",
    name: unitName ?? (loadout.name?.trim() || fallbackName),
    jobId: jobName,
    rank: (rank || "D") as HumanUnit["rank"],
    gender: isInteger(loadout.gender) && loadout.gender >= 0 ? loadout.gender : 0,
    parameters,
    equipmentSlots: equipment.slots,
    weaponId: equipment.weaponId,
    skills: skillResult.skills,
    // Per-unit progression: the loadout owner's awakening step count travels with THIS unit only.
    // It is copied, never re-derived, and never widened by another unit's allowance.
    ...(isInteger(loadout.awakening) && loadout.awakening >= 0
      ? { progression: { awakening: loadout.awakening, source: "PLAYER_LOADOUT" as const } }
      : {}),
  };
  return { unit, issues };
}

export type BattleSetupConversionResult = {
  setup: BattleSetup | null;
  issues: SetupIssue[];
  provenance: SetupProvenanceEntry[];
  units: HumanUnit[];
};

function uniqueNames(loadouts: SavedLoadout[]): { names: string[]; issues: SetupIssue[] } {
  const issues: SetupIssue[] = [];
  const used = new Set<string>();
  const requested = new Set(loadouts.map((loadout, index) => loadout.name?.trim() || loadout.jobName?.trim() || `Unit ${index + 1}`));
  const names = loadouts.map((loadout, index) => {
    const base = loadout.name?.trim() || loadout.jobName?.trim() || `Unit ${index + 1}`;
    let name = base;
    let counter = 2;
    while (used.has(name) || (name !== base && requested.has(name))) {
      name = `${base} (${counter})`;
      counter += 1;
    }
    used.add(name);
    return name;
  });
  return { names, issues };
}

/* ------------------------------------------------------------------ */
/* Declared household pet capacity                                     */
/* ------------------------------------------------------------------ */

/**
 * Declared owner job -> attached-pet slot capacity.
 *
 * User rule (`source: "user"`; no native per-job household capacity is recovered): EVERY job attaches
 * up to three household pets and Rancher attaches five. `DEFAULT_PET_SLOT_CAPACITY` is that shared
 * three, so only the jobs that differ from it are listed - no job is left unknown or unbounded.
 */
export const DEFAULT_PET_SLOT_CAPACITY = 3;

export const PET_SLOT_CAPACITY_BY_JOB: Record<string, { max: number; source: "user" }> = {
  Rancher: { max: 5, source: "user" },
};

export const PET_SLOT_CAPACITY_NOTE =
  "Every job attaches up to 3 household pets; Rancher attaches 5 (user rule). The pets listed are sent as the resident's complete household, in order.";

export function petSlotCapacity(jobName: string | undefined): { max: number; source: "user" } {
  if (!jobName) return { max: DEFAULT_PET_SLOT_CAPACITY, source: "user" };
  return PET_SLOT_CAPACITY_BY_JOB[jobName] ?? { max: DEFAULT_PET_SLOT_CAPACITY, source: "user" };
}

/**
 * How many declared pets exceed the job's capacity (3 for every job, 5 for Rancher). Single owner of
 * the comparison so the editor and the conversion agree on what "overfull" means.
 */
export function petCapacityOverBy(jobName: string | undefined, petCount: number): number {
  return Math.max(0, petCount - petSlotCapacity(jobName).max);
}

/* ------------------------------------------------------------------ */
/* Household pets                                                      */
/* ------------------------------------------------------------------ */

/**
 * Convert one declared household pet into the setup model.
 *
 * The species must be a row of the shared site monster catalog (the same owner `validateBattleSetup`
 * checks). The declared `level` becomes the `trainingLevel` on every monster parameter, exactly as
 * the owner's per-stat levels become training levels; `parameters` are explicit per-parameter
 * overrides. No monster stat curve is invented and no pet is placed or capped here.
 */
export function petUnitFromSaved(
  loadout: SavedLoadout,
  pet: SavedLoadoutPet,
  petIndex: number,
  name: string,
  issues: SetupIssue[],
): PetMonsterUnit | null {
  const basePath = `${loadout.jobName ?? ""}.householdPets[${petIndex}]`;
  const catalogEntry = MONSTER_BY_ID.get(pet.monsterId);
  if (!catalogEntry) {
    issues.push(
      error(
        "HOUSE_PET_NOT_ALLY_MONSTER",
        `${basePath}.monsterId`,
        `Pet species id ${JSON.stringify(pet.monsterId)} is not in the shared monster catalog; the pet was not sent to the runner.`,
      ),
    );
    return null;
  }
  const parameters: RawParameterSet = emptyParameters("monster");
  if (pet.level !== undefined) {
    if (!isInteger(pet.level) || pet.level < 1) {
      issues.push(
        error(
          "PET_LEVEL_INVALID",
          `${basePath}.level`,
          `Saved pet level ${JSON.stringify(pet.level)} for '${name}' must be an integer >= 1.`,
        ),
      );
    } else {
      for (const id of MONSTER_PARAMETER_IDS) {
        parameters[String(id)] = { ...parameters[String(id)], trainingLevel: pet.level };
      }
    }
  }
  for (const [key, override] of Object.entries(pet.parameters ?? {})) {
    const current: RawParameter | undefined = parameters[key];
    if (!current) {
      issues.push(
        error(
          "PET_PARAMETER_UNKNOWN",
          `${basePath}.parameters.${key}`,
          `Parameter ${key} is not a monster combat parameter; it was not added to '${name}'.`,
        ),
      );
      continue;
    }
    const int = (value: number | undefined, fallback: number) =>
      value === undefined ? fallback : isInteger(value) ? value : fallback;
    parameters[key] = {
      rawValue: int(override.rawValue, current.rawValue),
      rawMax: int(override.rawMax, current.rawMax),
      extraValue: int(override.extraValue, current.extraValue),
      extraMax: int(override.extraMax, current.extraMax),
      trainingLevel: int(override.trainingLevel, current.trainingLevel),
    };
  }
  // The species' canonical first skill belongs to the entity's initial skill list (native
  // `CreateMonster 0x147780c` loads `Monster.skillId`), and the runner reads this unit's `skills` as
  // that COMPLETE dataIds list. It is therefore put first here; an export that omitted it would
  // silently drop the native skill, and a species whose row declares none (-1) contributes nothing.
  // The value is a copied table field, never a guessed or substituted ability.
  const innateSkillId = MONSTER_INNATE_SKILL_BY_ID.get(pet.monsterId) ?? null;
  const declaredSkills: SkillSelection[] = [];
  (pet.skills ?? []).forEach((rawName, index) => {
    const skillName = typeof rawName === "string" ? rawName : "";
    if (!skillName) {
      issues.push(error("SKILL_NAME_UNRESOLVED", `${basePath}.skills[${index}]`, `Saved pet '${name}' has an empty skill entry.`));
      return;
    }
    const skillId = battleSkillIdForName(skillName);
    if (skillId === null) {
      issues.push(
        error(
          "SKILL_NAME_UNRESOLVED",
          `${basePath}.skills[${index}]`,
          `Pet skill '${skillName}' has no recovered skill catalog row whose name template renders to it; the pet was not sent to the runner.`,
        ),
      );
      return;
    }
    // A declared copy of the innate skill is the same slot; it is not emitted twice.
    if (skillId === innateSkillId) return;
    const declared = pet.skillInvocations?.[index];
    const invocationLevel = declared === 0 || declared === 1 || declared === 2 ? declared : 1;
    declaredSkills.push({ skillId, invocationLevel });
  });
  if (declaredSkills.length > 0 && (pet.skillInvocations ?? []).every((level) => level !== 0 && level !== 1 && level !== 2)) {
    issues.push(
      unknown(
        "INVOCATION_LEVEL_NOT_CAPTURED",
        `${basePath}.skills`,
        `Pet '${name}' has skills but no declared invocation levels; the native default level 1 was used instead of guessing.`,
      ),
    );
  }
  const skills: SkillSelection[] =
    innateSkillId === null
      ? declaredSkills
      : [{ skillId: innateSkillId, invocationLevel: 1 }, ...declaredSkills];
  return { kind: "monster", name, monsterId: pet.monsterId, parameters, skills };
}

/** Convert a list of saved loadouts into one legal player team setup. */
export function battleSetupFromLoadouts(
  loadouts: SavedLoadout[],
  shared: SharedLoadoutData,
  options: LoadoutConversionOptions = {},
): BattleSetupConversionResult {
  const issues: SetupIssue[] = [];
  const { names, issues: nameIssues } = uniqueNames(loadouts);
  issues.push(...nameIssues);

  const converted = loadouts.map((loadout, index) =>
    humanUnitFromLoadout(loadout, shared, options, names[index]),
  );
  const units = converted.map((entry) => entry.unit);
  for (const entry of converted) issues.push(...entry.issues);

  // Households are keyed by the CONVERTED unique unit name, because that is the name the adapter
  // and the runner match against the selected human. The declaration key set is authoritative: a
  // loadout with `householdPets` (even `[]`) is a declared house owner; an absent field is not.
  const usedPetNames = new Set(names);
  const households: Record<string, PetMonsterUnit[]> = {};
  loadouts.forEach((loadout, index) => {
    if (loadout.householdPets === undefined) return;
    const owner = names[index];
    const overCapacity = petCapacityOverBy(loadout.jobName, loadout.householdPets.length);
    if (overCapacity > 0) {
      const capacity = petSlotCapacity(loadout.jobName);
      issues.push(
        error(
          "PET_SLOT_CAPACITY_EXCEEDED",
          `${owner}.householdPets`,
          `'${owner}' (${loadout.jobName}) declares ${loadout.householdPets.length} household pets, above the ${capacity.max}-pet slot capacity for that job (every job 3, Rancher 5 - user rule). Remove ${overCapacity} pet(s) or change the job; this blocks the run instead of silently dropping the extra pet(s).`,
        ),
      );
    }
    const ownerPets: PetMonsterUnit[] = [];
    loadout.householdPets.forEach((savedPet, petIndex) => {
      const catalogName = MONSTER_BY_ID.get(savedPet.monsterId)?.name;
      const base = savedPet.name?.trim() || catalogName || `Pet ${petIndex + 1}`;
      let petName = base;
      let counter = 2;
      while (usedPetNames.has(petName)) {
        petName = `${base} (${counter})`;
        counter += 1;
      }
      if (petName !== base) {
        issues.push(
          warning(
            "PET_NAME_DERIVED",
            `${owner}.householdPets[${petIndex}].name`,
            `Pet name '${base}' is already used on this team; '${petName}' was derived because the simulator requires unique unit names.`,
          ),
        );
      }
      usedPetNames.add(petName);
      const convertedPet = petUnitFromSaved(loadout, savedPet, petIndex, petName, issues);
      if (convertedPet) ownerPets.push(convertedPet);
    });
    households[owner] = ownerPets;
  });

  const base = createDefaultBattleSetup(options.encounterId ?? 19);
  const setup: BattleSetup = {
    ...base,
    encounter: encounterSelection(options.encounterId ?? 19),
    defeatCount: options.defeatCount ?? 0,
    playerTeam: units,
    ...(Object.keys(households).length > 0 ? { households } : {}),
    partyLimit: isInteger(options.partyMax)
      ? {
          initialMax: 2,
          effectiveMax: options.partyMax as number,
          source:
            "Caller-supplied effective party maximum (valuable-effect ownership); the hard absolute maximum is still UNKNOWN.",
        }
      : base.partyLimit,
    seeds: options.seeds ?? { mathSeed: 0, libSeed: 0 },
    horizon: { tickLimit: options.tickLimit ?? base.horizon.tickLimit },
    inputs: [],
    holyHerbStock: options.holyHerbStock ?? 0,
    ...(options.items ? { items: options.items } : {}),
    ...(options.itemStock ? { itemStock: options.itemStock } : {}),
  };

  if (units.length === 0) {
    issues.push(error("LOADOUT_TEAM_EMPTY", "playerTeam", "Select at least one saved loadout."));
  }

  const validationIssues = units.length > 0 ? validateBattleSetup(setup) : [];
  const combined = [...issues, ...validationIssues];
  const blocked = combined.some((issue) => issue.category === "ERROR");

  return {
    setup: blocked ? null : setup,
    issues: combined,
    provenance: setupProvenance(units, setup),
    units,
  };
}

/* ------------------------------------------------------------------ */
/* Provenance of the converted setup                                   */
/* ------------------------------------------------------------------ */

export function setupProvenance(units: HumanUnit[], setup: BattleSetup): SetupProvenanceEntry[] {
  const entries: SetupProvenanceEntry[] = [
    {
      field: "playerTeam[].jobId",
      origin: "PLAYER_LOADOUT",
      note: "Saved loadout job name, resolved through the shared job table.",
    },
    {
      field: "playerTeam[].rank",
      origin: "PLAYER_LOADOUT",
      note: "Saved loadout rank.",
    },
    {
      field: "playerTeam[].name",
      origin: "PLAYER_LOADOUT",
      note: "Saved loadout name; duplicate names receive unique suffixes for the simulator.",
    },
    {
      field: "playerTeam[].gender",
      origin: units.every((unit) => unit.gender === 0) ? "UNKNOWN_NOT_CAPTURED" : "PLAYER_LOADOUT",
      note: "Index 0 = male, 1 = female (the image-array selectors accept 0/1 only). A gender the builder declared travels as a declared input; a character that never declared one keeps index 0, which is a labelled placeholder rather than a captured fact.",
    },
    {
      field: "playerTeam[].parameters[].rawValue/rawMax",
      origin: "SITE_DERIVED",
      note: "Shared job curve at the saved per-stat level. The save stores a stat level, not the native raw/training split. rawMax is the saved-level cap for the native bounded parameters HP(10)/MP(11)/Vigor(12) (JobData.ParamHasMaxValue 0x8007; check_combat_job_parameters.py) and the native 2147483647 uncapped sentinel for every other parameter, so equipment contributes to the effective value of the unbounded stats (see UNBOUNDED_PARAMETER_MAX). For HP/MP the pre-battle refill sets the effective value to the bounded maximum (fighter_parameter adds equipment to the maximum), so the effective baseline HP/MP includes gear and is higher than the rawValue shown here; do not read rawValue as the final pre-battle HP.",
    },
    {
      field: "playerTeam[].parameters[].extraValue/extraMax",
      origin: "SITE_DERIVED",
      note: "Saved resident-wide 'Water of ...' valuables, applied in the slot the recovered native dispatch writes: HP(10)/MP(11)/Vigor(12) raise the bounded maximum (extraMax, so the special launcher's pre-battle refill lifts the effective HP/MP current to that maximum while Vigor's current stays unrefilled), Attack(13)/Defence(14) raise the value (extraValue); see resident-valuable-effects.ts (ValuableSystem 0x160a244). A loadout with no captured residentStatItems reports RESIDENT_VALUABLES_NOT_CAPTURED (declared-input zero, not an assumed owned zero).",
    },
    {
      field: "playerTeam[].parameters[].trainingLevel",
      origin: "UNKNOWN_NOT_CAPTURED",
      note: "The native per-parameter training level is not stored; the saved stat level is used. The per-unit cap is the verified JobData.GetParamMaxLevel (Job.csv maxLevel) plus the native 30-per-awakening-step constant (SubForm.ResidentGrowLevelLimit 0x175ea90 -> Parameter.AddMaxLevel 0x1682a50, clamped at 999) times THIS unit's declared awakening (playerTeam[].progression.awakening). A level above that cap is an ERROR; a unit with no declared awakening reports TRAINING_LEVEL_ABOVE_NATIVE_MAX_UNDECLARED instead, and no party-wide allowance is applied.",
    },
    {
      field: "playerTeam[].progression.awakening",
      origin: "PLAYER_LOADOUT",
      note: "Awakening step count carried from the saved loadout (the preset owner's `awakening` field). Per unit: it is used only for that unit's training cap and is never shared across the team.",
    },
    {
      field: "playerTeam[].equipmentSlots[].id/level",
      origin: "PLAYER_LOADOUT",
      note: "Saved equipment name and level, resolved to the recovered combat equipment id by the shared site catalog (ids agree on every shared row).",
    },
    {
      field: "playerTeam[].equipmentSlots[].affinity",
      origin: "UNKNOWN_NOT_CAPTURED",
      note: "Per-instance affinity is not stored (declared input). The declared job-access verdict is mapped onto the native values 1 = normal, 0 = weak/half - max(1, trunc(contribution/2)), -1 = rejected (JobData.GetAffinity 0x16208d0), and the EquipData.CanEquip 0x1620e14 gate rejects exactly -1. A POSSESSED type-48 EQUIP_MASTER skill whose value equals the equipment type lifts BOTH -1 and 0 to 1 inside GetAffinity (predicate 0x162cc34), so the item is allowed and no halving applies.",
    },
    {
      field: "playerTeam[].skills[].skillId",
      origin: "SITE_DERIVED",
      note: "Recovered skill catalog row whose nameText/nameArg template renders the saved skill name.",
    },
    {
      field: "playerTeam[].skills[].invocationLevel",
      origin: "UNKNOWN_NOT_CAPTURED",
      note: "Invocation levels are not stored; the native default level 1 is used for every slot unless the caller supplies one (Entity.AddSkill 0x1472564 initialises to 1; GetInvocationRate 0x15dff08 indexes its rate array directly, so 0 = highest chance, 1 = ordinary, 2 = lowest).",
    },
    {
      field: "playerTeam[].visitor/leaderIdentity",
      origin: "RESEARCH_SYNTHETIC",
      note: "Not modelled by the loadout builder; false is used.",
    },
    {
      field: "encounter",
      origin: "RESEARCH_SYNTHETIC",
      note: "Chosen on this page (default Wairo Tank #19, the only full-art encounter), not part of the saved loadout.",
    },
    {
      field: "seeds/horizon.inputs/defeatCount/holyHerbStock/partyLimit.effectiveMax",
      origin: "RESEARCH_SYNTHETIC",
      note: `Research-only runner inputs, not player data. Current values: mathSeed ${setup.seeds.mathSeed}, libSeed ${setup.seeds.libSeed}, tickLimit ${setup.horizon.tickLimit}, inputs ${setup.inputs.length}, defeatCount ${setup.defeatCount}, holyHerbStock ${setup.holyHerbStock}.`,
    },
  ];
  if (units.length === 0) return entries;
  return entries;
}

/* ------------------------------------------------------------------ */
/* Loadout Builder handoff (shared by /loadout and /battle-setup)       */
/* ------------------------------------------------------------------ */

export const LOADOUT_HANDOFF_KEY = "ka_battle_setup_handoff";
export const LOADOUT_HANDOFF_SCHEMA = "ka-battle-setup-handoff-1";

export type LoadoutHandoffPayload = {
  schema: typeof LOADOUT_HANDOFF_SCHEMA;
  loadoutIds: string[];
};

/** Store the selected saved-loadout ids for /battle-setup. Returns false when storage is unavailable. */
export function writeLoadoutHandoff(loadoutIds: string[]): boolean {
  try {
    const payload: LoadoutHandoffPayload = { schema: LOADOUT_HANDOFF_SCHEMA, loadoutIds };
    sessionStorage.setItem(LOADOUT_HANDOFF_KEY, JSON.stringify(payload));
    return true;
  } catch {
    return false;
  }
}

/** Read and clear a pending handoff. Never throws; malformed payloads are reported as a message. */
export function readLoadoutHandoff(): { payload: LoadoutHandoffPayload | null; message: string | null } {
  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(LOADOUT_HANDOFF_KEY);
  } catch {
    return { payload: null, message: null };
  }
  if (!raw) return { payload: null, message: null };
  try {
    sessionStorage.removeItem(LOADOUT_HANDOFF_KEY);
  } catch {
    /* keep going: the payload is still usable */
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { payload: null, message: "Ignored a malformed loadout handoff payload." };
  }
  const candidate = parsed as { schema?: unknown; loadoutIds?: unknown };
  if (candidate?.schema !== LOADOUT_HANDOFF_SCHEMA || !Array.isArray(candidate.loadoutIds)) {
    return { payload: null, message: "Ignored a loadout handoff payload with an unknown schema." };
  }
  return {
    payload: {
      schema: LOADOUT_HANDOFF_SCHEMA,
      loadoutIds: candidate.loadoutIds.filter((id): id is string => typeof id === "string"),
    },
    message: null,
  };
}

/* ------------------------------------------------------------------ */
/* Runner rejections                                                   */
/* ------------------------------------------------------------------ */

/**
 * Turn a non-OK runner response into the exact native message.
 *
 * The Python runner reports `ScenarioError` text (for example a rejected skill id or a weapon that
 * is not part of the equipment contribution list). That text must reach the player instead of a
 * bare HTTP status.
 */
export function describeNativeRunnerRejection(status: number, body: string): string {
  const text = (body ?? "").trim();
  if (!text) return `runner responded ${status} with an empty body`;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = null;
  }
  if (parsed && typeof parsed === "object") {
    const record = parsed as Record<string, unknown>;
    for (const key of ["error", "message", "detail", "stderr", "reason"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return `${value.trim()} (runner HTTP ${status})`;
    }
  }
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  const nativeLine = [...lines].reverse().find((line) => /ScenarioError|Error|reject|invalid|Missing/i.test(line));
  const message = (nativeLine ?? lines[0] ?? text).trim();
  return `${message} (runner HTTP ${status})`;
}
