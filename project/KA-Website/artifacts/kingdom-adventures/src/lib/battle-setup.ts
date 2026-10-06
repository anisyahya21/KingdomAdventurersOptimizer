import encounterJson from "@/game-data/battle-encounter-variants.json";
import equipmentJson from "@/game-data/battle-equipment-catalog.json";
import nativeRecoveryItems from "@/game-data/native-recovery-items.json";
import skillJson from "@/game-data/battle-skill-catalog.json";
import monsterJson from "@/game-data/monster-sprites.json";
import monsterInnateJson from "@/game-data/native-monster-innate.json";
import nativeJobIdentities from "@/game-data/native-job-identity.json";
import { JOB_SKILL_DATA } from "@/lib/generated-job-skill-data";
import {
  NATIVE_DEFAULT_INVOCATION_LEVEL,
  skillActivationStatusFromFlags,
  type SkillActivationStatus,
} from "@/game-data/skill-job-admission";

/* ------------------------------------------------------------------ */
/* Catalogs                                                             */
/* ------------------------------------------------------------------ */

export type BattleEncounterVariant = {
  id: number;
  title: string;
  familyId: string;
  familyName: string;
  dayOfWeek: number | null;
  difficulty: 0 | 1 | 2 | 3;
  difficultyName: string;
  levelField: number;
  bossLevelField: number;
  boss: { monsterId: number; slotField: number; role: string; battleArtRecovered: boolean };
  followers: Array<{
    selectionIndex: number;
    entryType: number;
    monsterId: number;
    checkRate: number;
    role: string;
  }>;
  followersGrouped: Array<{
    monsterId: number;
    count: number;
    selectionIndices: number[];
    role: string;
  }>;
  enemyTotal: number;
  rewardGroupId: number;
  appearanceTerms: number[][];
  chipId: number;
  comment: string;
  distinctMonsterIds: number[];
  battleArtCoveredIds: number[];
  battleArtCoverage: { covered: number; total: number; allCovered: boolean };
  confidence: string;
};

type EncounterFile = {
  source: string;
  variants: BattleEncounterVariant[];
  battleArtIds: number[];
  limits: string[];
};

export type EquipmentCatalogEntry = {
  id: number;
  name: string;
  category: number;
  type: number;
  motion: number;
  attribute: number;
  parameters: number[][];
  shootingRange: number;
  flags: number;
  projectileFlag: boolean;
};

export type SkillCatalogEntry = {
  id: number;
  category: number;
  type: number;
  value: number;
  count: number;
  shootingRange: number;
  searchingRange: number;
  range: number;
  minMp: number;
  maxMp: number;
  motion: number;
  requiredEquipType: number;
  res: number;
  img: number;
  seb: number;
  impactImg: number;
  impactSeb: number;
  iconU: number;
  iconV: number;
  currency: number;
  nameText: string;
  nameArg: string;
  explainText: string;
  explainArg: string;
  flags: number;
};

export type MonsterCatalogEntry = {
  id: number;
  name: string;
  img: number;
  source: string;
  src: string;
  width: number;
  height: number;
};

const encounterFile = encounterJson as unknown as EncounterFile;
const equipmentFile = equipmentJson as unknown as {
  source: string;
  equipment: EquipmentCatalogEntry[];
  limits: string[];
};
const skillFile = skillJson as unknown as {
  source: string;
  skills: SkillCatalogEntry[];
  limits: string[];
};

export const ENCOUNTER_VARIANTS: BattleEncounterVariant[] = encounterFile.variants;
export const EQUIPMENT_CATALOG: EquipmentCatalogEntry[] = equipmentFile.equipment;
export const SKILL_CATALOG: SkillCatalogEntry[] = skillFile.skills;
export const MONSTER_CATALOG: MonsterCatalogEntry[] = monsterJson as MonsterCatalogEntry[];

export const ENCOUNTER_BY_ID = new Map(ENCOUNTER_VARIANTS.map((entry) => [entry.id, entry]));
export const EQUIPMENT_BY_ID = new Map(EQUIPMENT_CATALOG.map((entry) => [entry.id, entry]));
export const SKILL_BY_ID = new Map(SKILL_CATALOG.map((entry) => [entry.id, entry]));

/**
 * Render a recovered skill catalog row's display name, e.g. `<0>-Hit Attack` + `2` -> `2-Hit Attack`.
 * The catalog owner renders its own names; the loadout converter consumes it instead of keeping a
 * second renderer.
 */
export function renderSkillName(entry: { nameText: string; nameArg: string }): string {
  return entry.nameText.replace(/<0>/g, entry.nameArg);
}

/**
 * Activation applicability for one skill id from this catalog: `active` when the row carries the
 * native battle-skill bit, `permanently_active` when it does not (always-on / activity rows), and
 * `unknown` when the id has no recovered `SkillData` row.
 */
export function skillActivationStatus(skillId: number): SkillActivationStatus {
  return skillActivationStatusFromFlags(SKILL_BY_ID.get(skillId)?.flags ?? null);
}
export const MONSTER_BY_ID = new Map(MONSTER_CATALOG.map((entry) => [entry.id, entry]));

/**
 * Canonical per-species first (innate) skill from the original Monster table.
 *
 * Native `CreateMonster 0x147780c` loads the Monster row's single `skillId` into the entity's
 * initial skill list, so that skill is FIRST in the SkillComponent data. The runner takes an own
 * monster's declared `skills` list verbatim as the complete dataIds list, so an export that omits
 * this row silently drops the native skill. `null` is the table's own `-1` (no innate skill);
 * `undefined` means the species is not in the canonical Monster table.
 */
type MonsterInnateFile = {
  source: { table: string; route: string; sha256: string; rowCount: number; reader: string };
  crossCheck: { against: string; rows: number; mismatches: unknown[] };
  monsters: Array<{ id: number; name: string; skillId: number | null }>;
  limits: string[];
};

const monsterInnateFile = monsterInnateJson as unknown as MonsterInnateFile;

export const MONSTER_INNATE_SOURCE = monsterInnateFile.source;
export const MONSTER_INNATE_LIMITS: string[] = monsterInnateFile.limits;

/** Canonical species id -> its native first skill id, or `null` when the row declares none. */
export const MONSTER_INNATE_SKILL_BY_ID = new Map<number, number | null>(
  monsterInnateFile.monsters.map((entry) => [entry.id, entry.skillId]),
);

/* ------------------------------------------------------------------ */
/* Canonical recovery items (ExecuteItem 0x167b834 category 3)         */
/* ------------------------------------------------------------------ */

/**
 * Explicit battle-item row consumed by the authoritative scenario schema. These four fields are
 * exactly the ones `Main.AppData.ExecuteItem 0x167b834` classifies an item with; anything outside
 * `bonusCategory 3` / `bonusType 0..5` fails closed in the Python loader, so no row is invented.
 */
export type BattleItemRow = {
  bonusCategory: 3;
  bonusType: 0 | 1 | 2 | 3 | 4 | 5;
  bonusMinValue: number;
  bonusMaxValue: number;
};

export type CanonicalRecoveryItem = {
  id: number;
  name: string;
  aliases: string[];
  parameter: number;
  scope: "all" | "single";
  effect: string;
  row: BattleItemRow;
};

type RecoveryItemsFile = {
  source: { table: string; route: string; sha256: string };
  items: Array<{
    id: number;
    name: string;
    aliases?: string[];
    bonusCategory: number;
    bonusType: number;
    bonusMinValue: number;
    bonusMaxValue: number;
    parameter: number;
    scope: "all" | "single";
    effect: string;
  }>;
};

const recoveryItemsFile = nativeRecoveryItems as unknown as RecoveryItemsFile;

/** Rows copied from Item.txt (id 27 Large Potion, id 28 small, id 31 Holy Herb); never inferred. */
export const CANONICAL_RECOVERY_ITEMS: CanonicalRecoveryItem[] = recoveryItemsFile.items.map((entry) => ({
  id: entry.id,
  name: entry.name,
  aliases: entry.aliases ?? [],
  parameter: entry.parameter,
  scope: entry.scope,
  effect: entry.effect,
  row: {
    bonusCategory: 3,
    bonusType: entry.bonusType as BattleItemRow["bonusType"],
    bonusMinValue: entry.bonusMinValue,
    bonusMaxValue: entry.bonusMaxValue,
  },
}));

export const RECOVERY_ITEMS_SOURCE = recoveryItemsFile.source;

const RECOVERY_ITEM_BY_KEY = new Map<string, CanonicalRecoveryItem>();
for (const entry of CANONICAL_RECOVERY_ITEMS) {
  for (const key of [entry.name, ...entry.aliases]) RECOVERY_ITEM_BY_KEY.set(key.trim().toLowerCase(), entry);
}

/** Resolve an item by canonical Item.txt name or a recovered alias ("Large Potion"). */
export function canonicalRecoveryItem(name: string): CanonicalRecoveryItem | null {
  return RECOVERY_ITEM_BY_KEY.get(name.trim().toLowerCase()) ?? null;
}

/**
 * The user's "Large Potion" maps only to the proven Item.txt row id 27 `Recovery Potion (L)`
 * (bonusCategory 3 / bonusType 0 / 50-50, all-resident HP). When that row does not resolve the UI
 * must state this reason instead of substituting a guessed recovery row.
 */
export const LARGE_POTION_ITEM = canonicalRecoveryItem("Large Potion");
export const LARGE_POTION_UNAVAILABLE_REASON =
  "Large Potion is unavailable: the canonical recovery catalogue did not resolve a row (expected Item.txt id 27 Recovery Potion (L), bonusCategory 3 / bonusType 0). No recovery row is guessed.";

/** Explicit declared item map for canonical items, keyed by the canonical Item.txt name. */
export function declaredItemRows(items: CanonicalRecoveryItem[]): Record<string, BattleItemRow> {
  return Object.fromEntries(items.map((entry) => [entry.name, { ...entry.row }]));
}

export const FAMILY_DISPLAY_NAMES: Record<string, string> = {
  "saturday-kairobot-knight": "Saturday — Kairobot Knight",
  "tuesday-kairobot-mage": "Tuesday — Kairobot Mage",
  "thursday-aloha-kairobot": "Thursday — Aloha Kairobot",
  "sunday-kairo-kommander": "Sunday — Kairo Kommander",
  "wairo-dungeon": "Wairo Dungeon",
};

export const ENCOUNTER_FAMILIES = Array.from(
  new Map(
    ENCOUNTER_VARIANTS.map((variant) => [
      variant.familyId,
      {
        id: variant.familyId,
        name: FAMILY_DISPLAY_NAMES[variant.familyId] ?? variant.familyName,
        variants: ENCOUNTER_VARIANTS.filter((entry) => entry.familyId === variant.familyId),
      },
    ]),
  ).values(),
);

export const DIFFICULTY_NAMES = ["Easy", "Normal", "Hard", "Extreme"] as const;
export function difficultyName(difficulty: number): string {
  return DIFFICULTY_NAMES[difficulty] ?? `Difficulty ${difficulty}`;
}

export type EncounterCoverage = "FULL" | "PARTIAL";
export function encounterCoverage(variant: BattleEncounterVariant): EncounterCoverage {
  return variant.battleArtCoverage.allCovered ? "FULL" : "PARTIAL";
}

const JOB_RANK_ORDER = ["D", "C", "B", "A", "S"] as const;
/**
 * App-facing rank key. The generated catalog only has D/C/B/A/S, but the canonical Job.csv
 * producer also carries an `F` combat rank (`F Rank Scholar`, csvId 132), so `F` is a legal
 * saved-loadout rank key even though it is not part of `JOB_RANK_ORDER`.
 */
export type JobRank = (typeof JOB_RANK_ORDER)[number] | "F";

export type JobCatalogEntry = {
  /** Existing app-canonical job name. The native Job.csv rows are rank-specific records. */
  id: string;
  name: string;
  ranks: JobRank[];
};

export const JOB_CATALOG: JobCatalogEntry[] = Object.entries(JOB_SKILL_DATA)
  .map(([name, ranks]) => ({
    id: name,
    name,
    ranks: JOB_RANK_ORDER.filter((rank) => Boolean(ranks[rank as keyof typeof ranks])),
  }))
  .sort((a, b) => a.name.localeCompare(b.name));

export const JOB_BY_ID = new Map(JOB_CATALOG.map((entry) => [entry.id, entry]));

type NativeJobIdentityRow = {
  appName: string;
  sheetName: string;
  csvId: number;
  sheetRankToken: string;
  parameters: number[][];
};

/**
 * Reuses the one canonical combat job-identity producer (`native-job-identity.json`, copied from
 * `KA GameData - Job.csv`) instead of building a second catalog. `JOB_CATALOG` above only carries
 * the generated D/C/B/A/S rank keys, so the single combat sheet row whose rank token is not one of
 * those (`F Rank Scholar`, csvId 132) is absent from it. These maps expose that producer's sheet
 * rank tokens and per-parameter `maxLevel` blocks for job/rank resolution and cap validation.
 */
const NATIVE_JOB_ROWS = (nativeJobIdentities as unknown as { jobRows: NativeJobIdentityRow[] }).jobRows;
const NATIVE_PARAMETER_ORDER = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22] as const;

/** Sheet rank tokens per app job name, in sheet order (e.g. Scholar has the single token F). */
export const NATIVE_JOB_RANK_TOKENS = new Map<string, string[]>();
for (const row of NATIVE_JOB_ROWS) {
  const tokens = NATIVE_JOB_RANK_TOKENS.get(row.appName);
  if (tokens) {
    if (!tokens.includes(row.sheetRankToken)) tokens.push(row.sheetRankToken);
  } else {
    NATIVE_JOB_RANK_TOKENS.set(row.appName, [row.sheetRankToken]);
  }
}

/** Known app-facing rank keys, including the canonical `F` combat rank token. */
export const JOB_RANK_KEYS: Set<string> = new Set([...JOB_RANK_ORDER, "F"]);

/**
 * Per-native-parameter `maxLevel` (`JobData.GetParamMaxLevel 0x162c42c`) for a job's sheet row.
 * Returns null when the job/rank pair is not in the canonical Job.csv producer. The value is the
 * verified base cap only. Awakening raises it by the confirmed native step constant 30 each step
 * (`nativeParameterTrainingCaps`); the unit's step count is the declared input. Not to be confused
 * with `SubForm.GetMaxPotentialUpCount 0x1777530 = ValuableSystem.GetSpEffect(3)+5`, which bounds how
 * many awakening steps a unit may take (a COUNT), not how much one step adds.
 */
export function nativeJobParameterMaxLevels(jobId: string, rank: string): Record<number, number> | null {
  const rows = NATIVE_JOB_ROWS.filter((entry) => entry.appName === jobId);
  // The app-facing rank key equals the sheet token for the D/C/B/A/S jobs. The single canonical
  // `F Rank Scholar` row is stored by the shared owner under the app rank key `D` (its only rank),
  // so fall back to the job's unique sheet row instead of dropping its profile.
  const row = rows.find((entry) => entry.sheetRankToken === rank) ?? (rows.length === 1 ? rows[0] : undefined);
  if (!row) return null;
  const levels: Record<number, number> = {};
  row.parameters.forEach((block, index) => {
    const parameterId = NATIVE_PARAMETER_ORDER[index];
    if (parameterId !== undefined) levels[parameterId] = block[0];
  });
  return levels;
}

/**
 * Native training-limit step. `SubForm.ResidentGrowLevelLimit 0x175ea90` walks the resident's job
 * parameter row and calls `Parameter.AddMaxLevel 0x1682a50` with the constant 30 (`mov w1, #0x1e`);
 * `AddMaxLevel` does `maxLevel_ += value` unless `maxLevel_` holds the 0x7fffffff uncapped sentinel
 * (`ldr w8,[x0,#0x28]` / `cmp w8,#0x7fffffff` / `add w8,w8,w1` / `str w8,[x0,#0x28]`). The listing
 * has no multiplier and the grow step runs once per awakening/feeding step, so a unit with N steps
 * has a per-parameter limit of `JobData.GetParamMaxLevel + 30 * N`. The step constant is native; the
 * per-unit step count is declared input, because a raw parameter set does not store it.
 */
export const NATIVE_AWAKENING_MAX_LEVEL_STEP = 30 as const;
/**
 * Native ceiling for `Parameter.maxLevel_`: right after the add, `ResidentGrowLevelLimit` clamps a
 * maxLevel of 1000 or more back to 999 (`cmp w8, #0x3e8` / `mov w25, #0x3e7` / `str w25, [x0,
 * #0x28]`), for every parameter in the same pass.
 */
export const NATIVE_PARAMETER_MAX_LEVEL_CEILING = 999 as const;

/**
 * Per-unit progression metadata. `awakening` is the number of native grow/awakening steps this unit
 * received; the native limit increase it buys is `NATIVE_AWAKENING_MAX_LEVEL_STEP` per step. The
 * count is not in the raw parameter set, so it travels as an explicit per-unit declared input copied
 * from the loadout owner - never as a party-wide allowance another unit could borrow.
 */
export type HumanProgression = {
  awakening: number;
  source: "PLAYER_LOADOUT" | "SOURCE_INPUT" | "UNKNOWN_NOT_CAPTURED";
};

/**
 * Per-parameter training limits for one unit: the verified Job.csv `maxLevel`
 * (`nativeJobParameterMaxLevels`) plus one native grow step per declared awakening step, clamped at
 * the native 999 ceiling. Returns null when the job/rank pair is not in the canonical producer or the
 * declared step count is not a non-negative integer.
 */
export function nativeParameterTrainingCaps(
  jobId: string,
  rank: string,
  awakening: number,
): Record<number, number> | null {
  const base = nativeJobParameterMaxLevels(jobId, rank);
  if (!base || !isInteger(awakening) || awakening < 0) return null;
  const caps: Record<number, number> = {};
  for (const [id, maxLevel] of Object.entries(base)) {
    caps[Number(id)] = Math.min(
      NATIVE_PARAMETER_MAX_LEVEL_CEILING,
      maxLevel + NATIVE_AWAKENING_MAX_LEVEL_STEP * awakening,
    );
  }
  return caps;
}

/** Numeric indices proven by the image-array selectors; labels are deliberately not invented. */
export const GENDER_INDICES = [0, 1] as const;

export type EquipmentSlotKey = "weapon" | "shield" | "head" | "body" | "accessory";
export const EQUIPMENT_SLOT_ORDER: EquipmentSlotKey[] = [
  "weapon",
  "shield",
  "head",
  "body",
  "accessory",
];

export const NATIVE_EQUIPMENT_SLOTS: Record<EquipmentSlotKey, number> = {
  weapon: 0,
  shield: 1,
  head: 2,
  body: 3,
  accessory: 4,
};

/** `EquipComponent.SLOT_*` / catalog type mapping used by the recovered equipment table. */
export function equipmentSlotForEntry(entry: EquipmentCatalogEntry): EquipmentSlotKey {
  if (entry.type === 11) return "shield";
  if (entry.type === 13) return "head";
  if (entry.type === 12) return "body";
  if (entry.type === 14) return "accessory";
  return "weapon";
}

export function equipmentForSlot(slot: EquipmentSlotKey): EquipmentCatalogEntry[] {
  return EQUIPMENT_CATALOG.filter((entry) => equipmentSlotForEntry(entry) === slot);
}

/* ------------------------------------------------------------------ */
/* Setup model                                                          */
/* ------------------------------------------------------------------ */

export type RawParameter = {
  rawValue: number;
  rawMax: number;
  extraValue: number;
  extraMax: number;
  trainingLevel: number;
};

export type RawParameterSet = Record<string, RawParameter>;

export const HUMAN_PARAMETER_IDS = [10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22] as const;
export const MONSTER_PARAMETER_IDS = [10, 11, 13, 14, 15, 16, 19] as const;

export type EquipmentSlotSelection = {
  id: number;
  level: number;
  affinity: number;
};

export type SkillSelection = {
  skillId: number;
  invocationLevel: 0 | 1 | 2;
};

export type HumanUnit = {
  kind: "human";
  name: string;
  jobId: string;
  rank: JobRank;
  gender: number;
  parameters: RawParameterSet;
  equipmentSlots: Record<EquipmentSlotKey, EquipmentSlotSelection | null>;
  weaponId: number;
  skills: SkillSelection[];
  /**
   * Declared per-unit progression (awakening step count). Omitted when the unit has no declared
   * progression: the validator then reports an over-base training level as an explicit
   * UNKNOWN_NATIVE_RULE instead of inventing an allowance.
   */
  progression?: HumanProgression;
  humanFlags?: number;
  visitor?: boolean;
  leaderIdentity?: boolean;
  appearanceInputs?: {
    flag?: number;
    imgIds?: number[];
    source: "UNKNOWN" | "SOURCE_INPUT";
  };
};

export type PetMonsterUnit = {
  kind: "monster";
  name: string;
  monsterId: number;
  parameters: RawParameterSet;
  skills: SkillSelection[];
  petOwnerName?: string;
  ownerPlayer?: boolean;
};

export type PlayerUnit = HumanUnit | PetMonsterUnit;

export type BattleInputPhase = "before_fighters" | "after_fighters";

/**
 * One explicit, player-scheduled intervention. `holy_herb` spends from `holyHerbStock`;
 * `item` spends from `itemStock[item]` and names a row declared in `items`. The tick/phase pair
 * selects the exact engine tick and whether the input runs before or after the fighter updates
 * (the same ordering the authoritative engine tick loop exposes). A repeated schedule is a
 * declared player policy over a bounded inventory, never native AI behaviour.
 */
export type BattleInput =
  | { tick: number; phase: BattleInputPhase; type: "holy_herb" | "finish" }
  | { tick: number; phase: BattleInputPhase; type: "item"; item: string; target: "all" };

export type PlacementState = {
  cell: [number, number];
  position: [number, number, number];
  offset: [number, number, number];
  board: Record<string, number>;
  longBoard: Record<string, number>;
};

export const START_PROFILE_KIND = "isolated-scene0" as const;

/**
 * Declared isolated-scene0 start profile. These are DECLARED SIMULATION CONDITIONS, never captured
 * save data: `bossCell` is not captured (null means the follower spawn from the recovered
 * `World.CreateMonster 0x147780c` producer) and `startingStatus` is empty unless the user selects a
 * status. The only modelled source fields are the enemy Cell and AI board keys 19/20.
 */
export type StartProfile = {
  kind: typeof START_PROFILE_KIND;
  enemySpawnCell: [number, number];
  bossCell: [number, number] | null;
  startingStatus: Record<string, [number, number]>;
};

export const START_PROFILE_DECLARED_NOTE =
  "Declared simulation conditions: bossCell is not captured (defaults to the recovered follower spawn), startingStatus is empty by default, and the only modelled source fields are the enemy Cell/board keys 19/20 from World.CreateMonster 0x147780c.";

export function createDefaultStartProfile(): StartProfile {
  return { kind: START_PROFILE_KIND, enemySpawnCell: [0, 0], bossCell: null, startingStatus: {} };
}

/**
 * Native Ending->Finish is AUTOMATIC (user-authoritative): the results screen and the town return with
 * dispatched chests happen without any player confirmation. The exact native producer and timing are
 * UNRESOLVED (owned by the separate automatic-finish investigator), so the simulator reports dispatched
 * chests as diagnostics and never a trusted yield. `FinishPolicy` therefore only names a DECLARED
 * simulation cut: "at-horizon" keeps post-verdict native updates running to the declared tick limit,
 * and "on-verdict" is the diagnostic victory truncation. The former "after-ending" value modelled a
 * manual "confirm after 80 ending frames" step that the real game does NOT have; it is QUARANTINED and
 * is not offered as normal product policy (kept only so an older declared payload still round-trips).
 */
export type FinishPolicy = "at-horizon" | "after-ending" | "on-verdict";

/**
 * Honest shared status for the automatic native Finish. Every surface renders this instead of claiming
 * a trusted yield or a completed automatic model. No timing or native rule is asserted here.
 */
export const AUTOMATIC_FINISH_STATUS_NOTE =
  "Native Ending→Finish is AUTOMATIC: the results screen and the town return (with dispatched chests) happen without any player confirmation. The exact native producer and timing are UNRESOLVED (separate investigator), so automatic Finish is not modelled as complete and no dispatched-chest figure here is a trusted yield. The selector is a declared simulation cut, not that automatic policy: the horizon keeps post-verdict updates running to the declared tick limit, and the victory cut is a diagnostic truncation.";

export type EncounterSelection = {
  encounterId: number;
  familyId: string;
  difficulty: 0 | 1 | 2 | 3;
  sourceTitle: string;
};

export type BattleSetup = {
  schema: "ka-battle-setup-1";
  defeatCount: number;
  encounter: EncounterSelection;
  playerTeam: PlayerUnit[];
  /**
   * Explicit household completeness declaration for manual (non-native-import) setups.
   *
   * Maps a selected human house-owner name to that owner's COMPLETE ordered household lookup
   * result: the Ally+Monster staff assigned to that owner's house furniture, in native enumeration
   * order, with no count cap, filter or deduplication (recovered form.BattleForm$$CreateBossBattle
   * 0x16a8fa0 -> ecs.MonsterSystem$$SearchAllyMonstersInHouse 0x15bede8). `[]` declares an explicit
   * "no pets". The key set is the player's declaration of which selected humans are house owners;
   * membership is never inferred from a pet's name, position or stats. Omitting `households` means
   * no human is declared a house owner. There is no native-save importer, so nothing bypasses the
   * complete-list contract: a selected house owner without an entry is rejected, not read as none.
   */
  households?: Record<string, PetMonsterUnit[]>;
  partyLimit: {
    initialMax: 2;
    effectiveMax?: number;
    source: string;
  };
  seeds: { mathSeed: number; libSeed: number };
  horizon: { tickLimit: number };
  inputs: BattleInput[];
  holyHerbStock: number;
  /**
   * Optional explicit item rows (only recovered category-3 recovery items) and their finite counts.
   * Omitted for legacy payloads, which keeps the previous `holyHerbStock`-only behaviour.
   */
  items?: Record<string, BattleItemRow>;
  itemStock?: Record<string, number>;
  prePlacement?: Record<string, PlacementState>;
  startProfile?: StartProfile;
  finishPolicy?: FinishPolicy;
};

export const PARTY_LIMIT_INITIAL_MAX = 2 as const;
export const PARTY_LIMIT_SOURCE =
  "Native initial maximum is 2; runtime maximum is 2 + owned relevant valuable effects; hard absolute maximum UNKNOWN.";
/** Confirmed native skill-slot ceiling (see NATIVE_SKILL_SLOT_CEILING). The per-character capacity stays a declared input. */
export const SKILL_SLOT_CAP = 9 as const;
export const PRODUCT_RULE_AT_LEAST_ONE_HUMAN = true as const;

export function emptyParameters(kind: "human" | "monster"): RawParameterSet {
  const ids = kind === "human" ? HUMAN_PARAMETER_IDS : MONSTER_PARAMETER_IDS;
  return Object.fromEntries(
    ids.map((id) => [
      String(id),
      { rawValue: 0, rawMax: 0, extraValue: 0, extraMax: 0, trainingLevel: 1 },
    ]),
  );
}

export function emptyEquipmentSlots(): Record<EquipmentSlotKey, EquipmentSlotSelection | null> {
  return {
    weapon: null,
    shield: null,
    head: null,
    body: null,
    accessory: null,
  };
}

export function createHumanUnit(index: number): HumanUnit {
  return {
    kind: "human",
    name: `Human ${index}`,
    jobId: "Guard",
    rank: "D",
    gender: 0,
    parameters: emptyParameters("human"),
    equipmentSlots: emptyEquipmentSlots(),
    weaponId: 0,
    skills: [],
  };
}

export function createPetMonsterUnit(index: number, monsterId = 116): PetMonsterUnit {
  return {
    kind: "monster",
    name: `Pet ${index}`,
    monsterId,
    parameters: emptyParameters("monster"),
    skills: [],
  };
}

export function createDefaultBattleSetup(encounterId = 19): BattleSetup {
  const variant = ENCOUNTER_BY_ID.get(encounterId) ?? ENCOUNTER_VARIANTS[0];
  return {
    schema: "ka-battle-setup-1",
    defeatCount: 0,
    encounter: {
      encounterId: variant.id,
      familyId: variant.familyId,
      difficulty: variant.difficulty,
      sourceTitle: variant.title,
    },
    playerTeam: [createHumanUnit(1)],
    partyLimit: {
      initialMax: PARTY_LIMIT_INITIAL_MAX,
      source: PARTY_LIMIT_SOURCE,
    },
    seeds: { mathSeed: 0, libSeed: 0 },
    horizon: { tickLimit: 1000 },
    inputs: [],
    holyHerbStock: 0,
    startProfile: createDefaultStartProfile(),
    // Continue post-verdict native updates until the declared confirmation time.
    finishPolicy: "at-horizon",
  };
}

/* ------------------------------------------------------------------ */
/* Validation                                                           */
/* ------------------------------------------------------------------ */

export type SetupIssueCategory = "ERROR" | "WARNING" | "UNKNOWN_NATIVE_RULE";

export type SetupIssue = {
  category: SetupIssueCategory;
  code: string;
  path: string;
  message: string;
};

/** Problems the player can act on in the visual simulator. Other issues are audit diagnostics. */
export function isPlayerFacingBattleIssue(issue: SetupIssue): boolean {
  return issue.category === "ERROR" || ["PARTY_CAP_UNVALIDATED", "SKILL_REQUIRED_EQUIP_TYPE_MISMATCH", "PET_SLOT_CAPACITY_EXCEEDED"].includes(issue.code);
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

/**
 * Declared simulation conditions the save cannot carry. Every field is an explicit player input,
 * never an inferred value.
 *
 * Training limits are NOT an option here on purpose: the native step constant is fixed (30) and the
 * step count belongs to one unit, so it is carried on that unit (`HumanUnit.progression`) instead of
 * a party-wide allowance another unit could borrow.
 */
export type BattleSetupValidationOptions = Record<string, never>;

function validateParameters(
  unit: PlayerUnit,
  issues: SetupIssue[],
  options: BattleSetupValidationOptions,
): void {
  const required = unit.kind === "human" ? HUMAN_PARAMETER_IDS : MONSTER_PARAMETER_IDS;
  for (const id of required) {
    const parameter = unit.parameters[String(id)];
    if (!parameter) {
      issues.push({
        category: "ERROR",
        code: "PARAMETER_MISSING",
        path: `${unit.name}.parameters.${id}`,
        message: `Required parameter ${id} is missing.`,
      });
      continue;
    }
    for (const field of ["rawValue", "rawMax", "extraValue", "extraMax", "trainingLevel"] as const) {
      if (!isInteger(parameter[field])) {
        issues.push({
          category: "ERROR",
          code: "PARAMETER_MALFORMED",
          path: `${unit.name}.parameters.${id}.${field}`,
          message: `${field} must be an integer.`,
        });
      }
    }
    if (isInteger(parameter.trainingLevel) && parameter.trainingLevel < 1) {
      issues.push({
        category: "ERROR",
        code: "TRAINING_LEVEL_INVALID",
        path: `${unit.name}.parameters.${id}.trainingLevel`,
        message: "trainingLevel must be a positive integer; a training level is not bounded by int32 range alone.",
      });
    }
  }
  // Per-UNIT training cap: JobData.GetParamMaxLevel is the Job.csv maxLevel (canonical producer) and
  // SubForm.ResidentGrowLevelLimit 0x175ea90 adds the native constant 30 to every parameter of that
  // row once per awakening step, clamped at 999. The step count is declared per unit (never borrowed
  // from another unit), so a declared unit fails closed above its own cap and an undeclared unit is
  // reported as an explicit unknown instead of being accepted on int32 range alone.
  if (unit.kind === "human") {
    const progression = unit.progression;
    const awakening =
      progression && isInteger(progression.awakening) && progression.awakening >= 0
        ? progression.awakening
        : undefined;
    if (progression && awakening === undefined) {
      issues.push({
        category: "ERROR",
        code: "PROGRESSION_MALFORMED",
        path: `${unit.name}.progression.awakening`,
        message: `Declared awakening steps ${JSON.stringify(progression.awakening)} must be a non-negative integer.`,
      });
    }
    const maxLevels = nativeJobParameterMaxLevels(unit.jobId, unit.rank);
    if (maxLevels) {
      const caps =
        awakening === undefined
          ? maxLevels
          : nativeParameterTrainingCaps(unit.jobId, unit.rank, awakening) ?? maxLevels;
      for (const id of required) {
        const parameter = unit.parameters[String(id)];
        const cap = caps[id];
        const maxLevel = maxLevels[id];
        if (!parameter || cap === undefined || maxLevel === undefined || !isInteger(parameter.trainingLevel))
          continue;
        const level = parameter.trainingLevel;
        if (awakening === undefined) {
          if (level > maxLevel) {
            issues.push({
              category: "UNKNOWN_NATIVE_RULE",
              code: "TRAINING_LEVEL_ABOVE_NATIVE_MAX_UNDECLARED",
              path: `${unit.name}.parameters.${id}.trainingLevel`,
              message: `trainingLevel ${level} is above the verified Job.csv per-parameter maximum ${maxLevel} for '${unit.jobId}' rank ${unit.rank}, and this unit's awakening step count is not declared. The native limit increase is confirmed (SubForm.ResidentGrowLevelLimit 0x175ea90 adds the constant 30 per awakening step through Parameter.AddMaxLevel 0x1682a50, clamped at 999), so the missing input is the per-unit step count, not the formula; the overage stays a declared progression input instead of being accepted on int32 range alone.`,
            });
          }
        } else if (level > cap) {
          issues.push({
            category: "ERROR",
            code: "TRAINING_LEVEL_ABOVE_DECLARED_CAP",
            path: `${unit.name}.parameters.${id}.trainingLevel`,
            message: `trainingLevel ${level} exceeds this unit's declared cap ${cap}: Job.csv maximum ${maxLevel} for '${unit.jobId}' rank ${unit.rank} plus 30 per declared awakening step (${awakening} step(s), source ${progression?.source ?? "UNKNOWN"}${cap === NATIVE_PARAMETER_MAX_LEVEL_CEILING ? ", clamped at the native 999 ceiling" : ""}).`,
          });
        }
      }
    } else if (awakening !== undefined) {
      // Declared progression, but the job has no canonical Job.csv maxLevel profile (e.g. a job that
      // is not in the combat producer at all). The cap cannot be computed, so it is reported instead
      // of letting the level pass on int32 range alone.
      issues.push({
        category: "UNKNOWN_NATIVE_RULE",
        code: "PROGRESSION_CAP_UNVERIFIED",
        path: `${unit.name}.progression`,
        message: `'${unit.jobId}' rank ${unit.rank} has no canonical Job.csv per-parameter maxLevel profile, so the per-unit training cap (base + 30 per awakening step) cannot be computed. The declared awakening ${awakening} is carried unchanged, but the level is not verified as inside any cap.`,
      });
    }
  }
}

/**
 * Confirmed native skill-slot ceiling. `SkillComponent.ExtendSlot 0x14d0824(num,max)` grows
 * `maxSlotNum` by `num` and fails when `maxSlotNum+num > max`; `CanExtendSlot 0x14d09c0` is exactly
 * `maxSlotNum+num <= max`. All three live callers (GrowToAdult 0x157c174, UpdateKingRankUp
 * 0x16f65d4, AppData.ExecuteItem 0x167b834) pass `max=9`. This is the upper bound, not a claim that
 * every character owns nine slots.
 */
export const NATIVE_SKILL_SLOT_CEILING = SKILL_SLOT_CAP;

function validateSkills(unit: PlayerUnit, issues: SetupIssue[]): void {
  if (!Array.isArray(unit.skills)) {
    issues.push({
      category: "ERROR",
      code: "SKILLS_NOT_ARRAY",
      path: `${unit.name}.skills`,
      message: "skills must be an ordered array.",
    });
    return;
  }
  const weaponType = unit.kind === "human" ? EQUIPMENT_BY_ID.get(unit.weaponId)?.type ?? null : null;
  let sawPermanentlyActive = false;
  unit.skills.forEach((selection, index) => {
    const entry = SKILL_BY_ID.get(selection.skillId);
    if (!entry) {
      issues.push({
        category: "ERROR",
        code: "SKILL_UNKNOWN",
        path: `${unit.name}.skills[${index}]`,
        message: `Unknown skill id ${selection.skillId}.`,
      });
      return;
    }
    const activation = skillActivationStatusFromFlags(entry.flags);
    const validLevel = [0, 1, 2].includes(selection.invocationLevel);
    if (activation === "permanently_active") {
      // A permanently-active row has no trigger level to choose. `Entity.AddSkill 0x1472564`
      // still stores it with the native default, so the invocation array must stay aligned with
      // the skill array - but an activation requirement here would be a rule the game does not
      // have, so it is reported, never enforced.
      sawPermanentlyActive = true;
    } else if (!validLevel) {
      issues.push({
        category: "ERROR",
        code: "INVOCATION_LEVEL_INVALID",
        path: `${unit.name}.skills[${index}].invocationLevel`,
        message: `invocationLevel must be 0, 1 or 2 for an invoked skill (Skill '${renderSkillName(entry)}' carries SkillData.flags ${entry.flags}, which includes FLAG_FOR_BATTLE 0x8).`,
      });
    }
    // `CanUseSkill 0x15df0e0` requires the equipped weapon type to equal the skill's
    // `requiredEquipType` (special-combat.md:445). That decides whether the skill can EXECUTE; it
    // is not an ownership/equip gate (REVIEW, 20260920-local-strategy-ui/coverage). It is reported
    // as a non-blocking note for active skills only - a permanently-active row never runs the
    // CanUseSkill path at all - and the Python `combat_skills.can_use_skill` enforces the same
    // gate at run time.
    if (
      activation === "active" &&
      entry.requiredEquipType !== -1 &&
      weaponType !== null &&
      entry.requiredEquipType !== weaponType
    ) {
      issues.push({
        category: "UNKNOWN_NATIVE_RULE",
        code: "SKILL_REQUIRED_EQUIP_TYPE_MISMATCH",
        path: `${unit.name}.skills[${index}]`,
        message: `Skill '${renderSkillName(entry)}' (id ${entry.id}) requires equipped weapon type ${entry.requiredEquipType} (CanUseSkill 0x15df0e0), but '${unit.name}' has weapon type ${weaponType}. CanUseSkill decides whether the skill can activate, not whether owning it is forbidden, so this does not block the setup; the recovered Python eligibility check refuses the execution instead.`,
      });
    }
  });
  if (sawPermanentlyActive) {
    issues.push({
      category: "UNKNOWN_NATIVE_RULE",
      code: "SKILL_PERMANENTLY_ACTIVE_NO_ACTIVATION",
      path: `${unit.name}.skills`,
      message: `At least one selected skill has no battle invocation: its SkillData.flags lacks FLAG_FOR_BATTLE 0x8, so CanUseSkill 0x15df0e0 never invokes it (category-2 activity rows are rejected; always-on rows such as type-48 EQUIP_MASTER and the auto-recovery rows are permanently active). Its stored invocation level is the native default ${NATIVE_DEFAULT_INVOCATION_LEVEL} (Entity.AddSkill 0x1472564) and is kept only so the invocation array stays aligned with the skill array.`,
    });
  }
  if (unit.skills.length > NATIVE_SKILL_SLOT_CEILING) {
    issues.push({
      category: "ERROR",
      code: "SKILL_SLOT_CAP_EXCEEDED",
      path: `${unit.name}.skills`,
      message: `Native skill-slot ceiling is ${NATIVE_SKILL_SLOT_CEILING} (SkillComponent.ExtendSlot 0x14d0824 fails when maxSlotNum+num exceeds the bound, and every ExtendSlot caller passes max=9, so CanExtendSlot is maxSlotNum+num <= 9). ${unit.skills.length} slots were selected.`,
    });
  } else {
    issues.push({
      category: "UNKNOWN_NATIVE_RULE",
      code: "SKILL_SLOT_CAP_PER_CHARACTER_DECLARED",
      path: `${unit.name}.skills`,
      message: `Within the confirmed native ceiling of ${NATIVE_SKILL_SLOT_CEILING} skill slots (all SkillComponent.ExtendSlot callers pass max=9). The ceiling is universal and is not a claim that every character owns ${NATIVE_SKILL_SLOT_CEILING} slots: a character's own maxSlotNum starts from JobData.slotsNum plus owned upgrades, which the save does not store, so the per-character capacity stays a declared input.`,
    });
  }
}

function validateHumanEquipment(unit: HumanUnit, issues: SetupIssue[]): void {
  for (const slot of EQUIPMENT_SLOT_ORDER) {
    const selection = unit.equipmentSlots[slot];
    if (!selection) continue;
    const entry = EQUIPMENT_BY_ID.get(selection.id);
    if (!entry) {
      issues.push({
        category: "ERROR",
        code: "EQUIPMENT_UNKNOWN",
        path: `${unit.name}.equipmentSlots.${slot}`,
        message: `Unknown equipment id ${selection.id}.`,
      });
      continue;
    }
    if (equipmentSlotForEntry(entry) !== slot) {
      issues.push({
        category: "ERROR",
        code: "EQUIPMENT_SLOT_MISMATCH",
        path: `${unit.name}.equipmentSlots.${slot}`,
        message: `Equipment ${selection.id} is not legal for slot ${slot}.`,
      });
    }
    if (!isInteger(selection.level) || selection.level < 1) {
      issues.push({
        category: "ERROR",
        code: "EQUIPMENT_LEVEL_INVALID",
        path: `${unit.name}.equipmentSlots.${slot}.level`,
        message: "Equipment level must be a positive integer.",
      });
    }
    if (!isInteger(selection.affinity)) {
      issues.push({
        category: "ERROR",
        code: "AFFINITY_INVALID",
        path: `${unit.name}.equipmentSlots.${slot}.affinity`,
        message: "Equipment affinity must be an integer (native -1 rejects, 0 halves, otherwise normal).",
      });
    } else if (selection.affinity < 0) {
      issues.push({
        category: "ERROR",
        code: "AFFINITY_REJECTED",
        path: `${unit.name}.equipmentSlots.${slot}.affinity`,
        message: `Equipment affinity ${selection.affinity} is the native rejection value (EquipData.CanEquip / JobData.GetAffinity -1); a rejected item must not reach the runner.`,
      });
    }
  }
  const weapon = EQUIPMENT_BY_ID.get(unit.weaponId);
  if (!weapon) {
    issues.push({
      category: "ERROR",
      code: "WEAPON_UNKNOWN",
      path: `${unit.name}.weaponId`,
      message: `Unknown weapon id ${unit.weaponId}.`,
    });
  } else if (equipmentSlotForEntry(weapon) !== "weapon") {
    issues.push({
      category: "ERROR",
      code: "WEAPON_NOT_WEAPON",
      path: `${unit.name}.weaponId`,
      message: `Equipment ${unit.weaponId} is not a weapon record.`,
    });
  } else if (unit.weaponId !== 0 && unit.equipmentSlots.weapon?.id !== unit.weaponId) {
    issues.push({
      category: "ERROR",
      code: "WEAPON_SLOT_MISMATCH",
      path: `${unit.name}.weaponId`,
      message: "weaponId must match the weapon equipment slot.",
    });
  }
  /*
   * Equipment id 0 is the recovered `Bare-Handed` weapon record (`category 0`), and the
   * authoritative scenario validator exempts exactly that id from the equipment-contribution
   * list (`weaponId != 0` is the stated inclusion rule). An unarmed human is therefore valid
   * with `weaponId 0` and an empty weapon slot, while a non-zero weapon in the slot beside
   * `weaponId 0` stays contradictory.
   */
  const weaponSlotId = unit.equipmentSlots.weapon?.id;
  if (
    unit.weaponId === 0 &&
    weaponSlotId !== undefined &&
    weaponSlotId !== 0
  ) {
    issues.push({
      category: "ERROR",
      code: "WEAPON_SLOT_MISMATCH",
      path: `${unit.name}.weaponId`,
      message: "weaponId 0 (Bare-Handed) cannot sit beside a different weapon slot selection.",
    });
  }
  issues.push({
    category: "UNKNOWN_NATIVE_RULE",
    code: "JOB_EQUIPMENT_COMPATIBILITY_UNKNOWN",
    path: `${unit.name}.equipmentSlots`,
    message: "Job/equipment compatibility is not yet proven and is not rejected.",
  });
  issues.push({
    category: "WARNING",
    code: "HUMAN_APPEARANCE_INCOMPLETE",
    path: `${unit.name}.appearanceInputs`,
    message: "Human appearance inputs are incomplete; the unit is allowed with PARTIAL visual coverage.",
  });
}

/**
 * Mirror the authoritative scenario loader's item/input rules (`combat_scenario.load_scenario`) so a
 * malformed policy is an ERROR here instead of an opaque rejection from the runner.
 */
function validateBattleInputs(setup: BattleSetup, issues: SetupIssue[]): void {
  const items = setup.items ?? {};
  for (const [name, row] of Object.entries(items)) {
    if (!name.trim()) {
      issues.push({
        category: "ERROR",
        code: "ITEM_NAME_EMPTY",
        path: "items",
        message: "An item row needs a non-empty explicit name.",
      });
    }
    for (const field of ["bonusCategory", "bonusType", "bonusMinValue", "bonusMaxValue"] as const) {
      if (!isInteger(row?.[field])) {
        issues.push({
          category: "ERROR",
          code: "ITEM_ROW_MALFORMED",
          path: `items.${name}.${field}`,
          message: `Item row ${name} needs an integer ${field}.`,
        });
      }
    }
    if (!isInteger(row?.bonusCategory) || row.bonusCategory !== 3 || !isInteger(row?.bonusType) || row.bonusType < 0 || row.bonusType > 5) {
      issues.push({
        category: "ERROR",
        code: "ITEM_EFFECT_UNSUPPORTED",
        path: `items.${name}`,
        message:
          "Only the recovered ExecuteItem recovery items are modelled (bonusCategory 3, bonusType 0..5); a different row fails closed.",
      });
    }
  }
  for (const [name, count] of Object.entries(setup.itemStock ?? {})) {
    if (!isInteger(count) || count < 0) {
      issues.push({
        category: "ERROR",
        code: "ITEM_STOCK_MALFORMED",
        path: `itemStock.${name}`,
        message: `Item stock for ${name} must be a non-negative integer.`,
      });
    }
  }
  if (!isInteger(setup.holyHerbStock) || setup.holyHerbStock < 0) {
    issues.push({
      category: "ERROR",
      code: "HOLY_HERB_STOCK_MALFORMED",
      path: "holyHerbStock",
      message: "Holy Herb stock must be a non-negative integer (0 is a finite empty budget).",
    });
  }
  setup.inputs.forEach((input, index) => {
    if (!isInteger(input?.tick) || input.tick < 0) {
      issues.push({
        category: "ERROR",
        code: "INPUT_TICK_INVALID",
        path: `inputs[${index}].tick`,
        message: "An input tick must be a non-negative integer.",
      });
    }
    if (input?.phase !== "before_fighters" && input?.phase !== "after_fighters") {
      issues.push({
        category: "ERROR",
        code: "INPUT_PHASE_UNSUPPORTED",
        path: `inputs[${index}].phase`,
        message: "An input phase must be before_fighters or after_fighters.",
      });
    }
    if (input?.type !== "holy_herb" && input?.type !== "finish" && input?.type !== "item") {
      issues.push({
        category: "ERROR",
        code: "INPUT_TYPE_UNSUPPORTED",
        path: `inputs[${index}].type`,
        message: "An input type must be holy_herb, item or finish.",
      });
      return;
    }
    if (input.type !== "item") return;
    if (!items[input.item]) {
      issues.push({
        category: "ERROR",
        code: "ITEM_INPUT_UNDECLARED",
        path: `inputs[${index}].item`,
        message: `The scheduled item ${input.item} has no declared item row.`,
      });
    }
    if (!(input.item in (setup.itemStock ?? {}))) {
      issues.push({
        category: "ERROR",
        code: "ITEM_INPUT_NO_STOCK",
        path: `inputs[${index}].item`,
        message: `The scheduled item ${input.item} needs an explicit finite stock entry.`,
      });
    }
    if (input.target !== "all") {
      issues.push({
        category: "ERROR",
        code: "ITEM_INPUT_TARGET_SCOPE",
        path: `inputs[${index}].target`,
        message: "A battle item input uses the explicit all-residents scope (target 'all').",
      });
    }
  });
}

export function validateBattleSetup(
  setup: BattleSetup,
  options: BattleSetupValidationOptions = {},
): SetupIssue[] {
  const issues: SetupIssue[] = [];
  if (setup.prePlacement && setup.startProfile) {
    issues.push({
      category: "ERROR",
      code: "PREPLACEMENT_START_PROFILE_CONFLICT",
      path: "startProfile",
      message: "Supply either captured prePlacement or a declared startProfile, not both.",
    });
  }
  const variant = ENCOUNTER_BY_ID.get(setup.encounter?.encounterId);
  if (!isInteger(setup.defeatCount) || setup.defeatCount < 0) {
    issues.push({
      category: "ERROR",
      code: "DEFEAT_COUNT_INVALID",
      path: "defeatCount",
      message: "defeatCount must be a non-negative integer.",
    });
  }
  if (!variant) {
    issues.push({
      category: "ERROR",
      code: "ENCOUNTER_UNKNOWN",
      path: "encounter.encounterId",
      message: "Encounter id must be one of the 20 native records.",
    });
  } else {
    if (encounterCoverage(variant) === "PARTIAL") {
      issues.push({
        category: "WARNING",
        code: "ENCOUNTER_ART_PARTIAL",
        path: "encounter",
        message: "Combat data recovered; some enemy battle sprites are not yet recovered.",
      });
    }
  }

  if (!Array.isArray(setup.playerTeam) || setup.playerTeam.length === 0) {
    issues.push({
      category: "ERROR",
      code: "TEAM_EMPTY",
      path: "playerTeam",
      message: "The player team cannot be empty.",
    });
    return issues;
  }

  const names = new Set<string>();
  const humanNames = new Set<string>();
  let humanCount = 0;
  setup.playerTeam.forEach((unit, index) => {
    if (!unit || !unit.name) {
      issues.push({
        category: "ERROR",
        code: "UNIT_NAME_MISSING",
        path: `playerTeam[${index}]`,
        message: "Every unit needs a name.",
      });
      return;
    }
    if (names.has(unit.name)) {
      issues.push({
        category: "ERROR",
        code: "UNIT_NAME_DUPLICATE",
        path: `playerTeam[${index}].name`,
        message: "Unit names must be unique for the simulator.",
      });
    }
    names.add(unit.name);
    if (unit.kind === "human") {
      humanCount += 1;
      humanNames.add(unit.name);
      const catalogEntry = JOB_BY_ID.get(unit.jobId);
      const nativeRankTokens = NATIVE_JOB_RANK_TOKENS.get(unit.jobId);
      if (!catalogEntry && !nativeRankTokens) {
        issues.push({
          category: "ERROR",
          code: "JOB_UNKNOWN",
          path: `playerTeam[${index}].jobId`,
          message: `Unknown job '${unit.jobId}'.`,
        });
      } else if (catalogEntry) {
        if (!catalogEntry.ranks.includes(unit.rank)) {
          issues.push({
            category: "ERROR",
            code: "RANK_UNKNOWN",
            path: `playerTeam[${index}].rank`,
            message: `Rank ${unit.rank} is not present for job '${unit.jobId}' (generated catalog ranks ${catalogEntry.ranks.join("/")}).`,
          });
        }
      } else if (!JOB_RANK_KEYS.has(unit.rank)) {
        // Canonical Job.csv job absent from the generated catalog (e.g. the single F Rank Scholar):
        // the saved rank key is the site's own storage key and is not asserted to equal the sheet
        // rank token, so no rank gate is invented for it. It must still be a known rank key.
        issues.push({
          category: "ERROR",
          code: "RANK_UNKNOWN",
          path: `playerTeam[${index}].rank`,
          message: `Rank ${unit.rank} is not a known rank key for canonical Job.csv job '${unit.jobId}' (sheet rank token(s) ${(nativeRankTokens ?? []).join("/") || "none"}).`,
        });
      }
      if (!GENDER_INDICES.includes(unit.gender as 0 | 1)) {
        issues.push({
          category: "ERROR",
          code: "GENDER_INVALID",
          path: `playerTeam[${index}].gender`,
          message: "Gender index must be 0 or 1; no male/female labels are inferred.",
        });
      }
      validateHumanEquipment(unit, issues);
    } else {
      if (!MONSTER_BY_ID.has(unit.monsterId)) {
        issues.push({
          category: "ERROR",
          code: "MONSTER_UNKNOWN",
          path: `playerTeam[${index}].monsterId`,
          message: `Unknown monster id ${unit.monsterId}.`,
        });
      }
      issues.push({
        category: unit.petOwnerName ? "ERROR" : "UNKNOWN_NATIVE_RULE",
        code: unit.petOwnerName ? "PET_OWNER_IN_PLAYER_TEAM" : "PET_MEMBERSHIP_MISSING_DATA",
        path: `playerTeam[${index}]`,
        message: unit.petOwnerName
          ? "House pets are declared under households[owner], not as a petOwnerName monster in playerTeam; declare the owner's complete household list instead."
          : "This monster is not declared as any owner's household pet; its native owner membership is missing. Declare it under households[owner] or remove it.",
      });
    }
    validateParameters(unit, issues, options);
    validateSkills(unit, issues);
  });

  // Explicit household completeness declaration. Keys are selected human house owners and each
  // value must be the owner's COMPLETE ordered household list (`[]` = an explicit "no pets"). The
  // recovered rule has no cap, filter or dedup, and membership is never inferred.
  for (const [owner, pets] of Object.entries(setup.households ?? {})) {
    const ownerPath = `households.${owner}`;
    if (!Array.isArray(pets)) {
      issues.push({
        category: "ERROR",
        code: "HOUSEHOLD_PETS_MALFORMED",
        path: ownerPath,
        message: "A household entry must be a list of the owner's complete household pets.",
      });
      continue;
    }
    if (!humanNames.has(owner)) {
      issues.push({
        category: "ERROR",
        code: "PET_OWNER_NOT_SELECTED_HUMAN",
        path: ownerPath,
        message: `Pet owner '${owner}' is not a selected human unit.`,
      });
    }
    pets.forEach((pet, petIndex) => {
      const petPath = `${ownerPath}[${petIndex}]`;
      if (!pet || pet.kind !== "monster" || !MONSTER_BY_ID.has(pet.monsterId)) {
        issues.push({
          category: "ERROR",
          code: "HOUSE_PET_NOT_ALLY_MONSTER",
          path: petPath,
          message: "A household pet must be a known ally monster (native predicate 0x15bf900 needs both Ally and Monster components).",
        });
        return;
      }
      if (pet.petOwnerName !== undefined && pet.petOwnerName !== owner) {
        issues.push({
          category: "ERROR",
          code: "PET_OWNER_CONFLICT",
          path: `${petPath}.petOwnerName`,
          message: `Household pet '${pet.name}' declares owner '${pet.petOwnerName}' under '${owner}'.`,
        });
      }
      if (names.has(pet.name)) {
        issues.push({
          category: "ERROR",
          code: "UNIT_NAME_DUPLICATE",
          path: `${petPath}.name`,
          message: "Unit names must be unique for the simulator.",
        });
      }
      names.add(pet.name);
      validateParameters(pet, issues, options);
      validateSkills(pet, issues);
    });
  }

  validateBattleInputs(setup, issues);

  if (humanCount === 0) {
    issues.push({
      category: "ERROR",
      code: "PRODUCT_RULE_NO_HUMAN",
      path: "playerTeam",
      message: "PRODUCT RULE: at least one human is required. This is not a recovered native constant.",
    });
  }

  const effectiveMax = setup.partyLimit?.effectiveMax;
  if (isInteger(effectiveMax)) {
    if (setup.playerTeam.length > effectiveMax) {
      issues.push({
        category: "ERROR",
        code: "PARTY_CAP_EXCEEDED",
        path: "playerTeam",
        message: `Party size ${setup.playerTeam.length} exceeds the supplied effective maximum ${effectiveMax}.`,
      });
    }
  } else if (setup.playerTeam.length > PARTY_LIMIT_INITIAL_MAX) {
    issues.push({
      category: "WARNING",
      code: "PARTY_CAP_UNVALIDATED",
      path: "partyLimit",
      message: "Team size is above the base capacity of 2; extra slots from valuables have not been entered.",
    });
  }

  return issues;
}

/* ------------------------------------------------------------------ */
/* Deterministic JSON                                                   */
/* ------------------------------------------------------------------ */

function stableSort(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableSort);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, entry]) => [key, stableSort(entry)]),
    );
  }
  return value;
}

export function serializeBattleSetup(setup: BattleSetup): string {
  return JSON.stringify(stableSort(setup), null, 2) + "\n";
}

function isInvocationLevel(value: number): value is 0 | 1 | 2 {
  return value === 0 || value === 1 || value === 2;
}

function normalizeSkills(raw: unknown): SkillSelection[] {
  if (Array.isArray(raw)) {
    if (raw.every((entry) => entry && typeof entry === "object" && "skillId" in entry)) {
      return (raw as SkillSelection[]).map((entry) => ({
        skillId: Number(entry.skillId),
        // An absent or malformed level becomes the native default 1 (`Entity.AddSkill 0x1472564`
        // initialises invocation levels to 1 and `SetSkillAt 0x14d0214` resets the slot to 1), so a
        // permanently-active skill round-trips without inventing an activation requirement and the
        // invocation array stays aligned with the skill array. An explicitly stored 0/1/2 is kept.
        invocationLevel: isInvocationLevel(Number(entry.invocationLevel))
          ? (Number(entry.invocationLevel) as 0 | 1 | 2)
          : NATIVE_DEFAULT_INVOCATION_LEVEL,
      }));
    }
    if (raw.every((entry) => typeof entry === "number")) {
      return (raw as number[]).map((skillId) => ({ skillId, invocationLevel: 0 }));
    }
  }
  return [];
}

/**
 * Import normalization for the per-unit progression metadata. The declared awakening step count is
 * preserved (it is what the per-unit training cap is computed from); an absent or malformed block
 * stays absent so the validator reports the missing input instead of assuming zero.
 */
function normalizeProgression(raw: unknown): HumanProgression | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const block = raw as { awakening?: unknown; source?: unknown };
  const awakening = Number(block.awakening);
  if (!Number.isInteger(awakening) || awakening < 0) return undefined;
  const source =
    block.source === "PLAYER_LOADOUT" || block.source === "SOURCE_INPUT"
      ? block.source
      : "UNKNOWN_NOT_CAPTURED";
  return { awakening, source };
}

export function importBattleSetup(json: string): { setup?: BattleSetup; issues: SetupIssue[] } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return {
      issues: [
        {
          category: "ERROR",
          code: "JSON_INVALID",
          path: "$",
          message: "The setup JSON is not valid JSON.",
        },
      ],
    };
  }
  if (!parsed || typeof parsed !== "object") {
    return {
      issues: [
        {
          category: "ERROR",
          code: "SCHEMA_INVALID",
          path: "$",
          message: "The setup must be an object.",
        },
      ],
    };
  }
  const raw = parsed as Omit<BattleSetup, "playerTeam"> & {
    playerTeam?: Array<Record<string, unknown>>;
  };
  if (raw.schema !== "ka-battle-setup-1") {
    return {
      issues: [
        {
          category: "ERROR",
          code: "SCHEMA_INVALID",
          path: "schema",
          message: "Unsupported BattleSetup schema.",
        },
      ],
    };
  }
  const playerTeam = (raw.playerTeam ?? []).map((unit) => {
    if (unit.kind === "human") {
      return {
        ...unit,
        kind: "human" as const,
        parameters: (unit.parameters ?? emptyParameters("human")) as RawParameterSet,
        equipmentSlots: (unit.equipmentSlots ?? emptyEquipmentSlots()) as Record<
          EquipmentSlotKey,
          EquipmentSlotSelection | null
        >,
        skills: normalizeSkills(unit.skills),
        ...(normalizeProgression(unit.progression)
          ? { progression: normalizeProgression(unit.progression) }
          : {}),
      } as HumanUnit;
    }
    return {
      ...unit,
      kind: "monster" as const,
      parameters: (unit.parameters ?? emptyParameters("monster")) as RawParameterSet,
      skills: normalizeSkills(unit.skills),
    } as PetMonsterUnit;
  });
  // Household declarations are preserved only when present, so a payload without them keeps its
  // exact serialized form (no empty `households` key is invented on import).
  const households =
    !raw.households || typeof raw.households !== "object" || Array.isArray(raw.households)
      ? undefined
      : Object.fromEntries(
          Object.entries(raw.households as Record<string, unknown>).map(([owner, pets]) => [
            owner,
            (Array.isArray(pets) ? pets : []).map((pet) => {
              const entry = pet as Record<string, unknown>;
              return {
                ...entry,
                kind: "monster" as const,
                parameters: (entry.parameters ?? emptyParameters("monster")) as RawParameterSet,
                skills: normalizeSkills(entry.skills),
              } as PetMonsterUnit;
            }),
          ]),
        );
  const setup: BattleSetup = {
    schema: "ka-battle-setup-1",
    defeatCount: Number(raw.defeatCount ?? 0),
    encounter: (raw.encounter ?? createDefaultBattleSetup().encounter) as EncounterSelection,
    playerTeam,
    ...(households ? { households } : {}),
    partyLimit: (raw.partyLimit ?? createDefaultBattleSetup().partyLimit) as BattleSetup["partyLimit"],
    seeds: (raw.seeds ?? { mathSeed: 0, libSeed: 0 }) as BattleSetup["seeds"],
    horizon: (raw.horizon ?? { tickLimit: 1000 }) as BattleSetup["horizon"],
    inputs: (raw.inputs ?? []) as BattleInput[],
    holyHerbStock: Number(raw.holyHerbStock ?? 0),
    ...(raw.items ? { items: raw.items as BattleSetup["items"] } : {}),
    ...(raw.itemStock ? { itemStock: raw.itemStock as BattleSetup["itemStock"] } : {}),
    prePlacement: raw.prePlacement as BattleSetup["prePlacement"],
    startProfile: (raw.startProfile ?? createDefaultStartProfile()) as BattleSetup["startProfile"],
    finishPolicy: (raw.finishPolicy ?? "at-horizon") as BattleSetup["finishPolicy"],
  };
  return { setup, issues: validateBattleSetup(setup) };
}
