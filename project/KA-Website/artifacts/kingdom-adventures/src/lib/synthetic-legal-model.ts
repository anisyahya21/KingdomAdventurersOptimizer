import type { SavedLoadout } from "@/lib/battle-legality";
import type { StatKey } from "@/game-data/stat-parameter-ids";

export const PLAYER_PROFILE_SCHEMA = "ka-player-profile-1" as const;
export const NORMALIZED_TARGET_SCHEMA = "ka-normalized-optimizer-target-1" as const;

export type PlayerCharacter = {
  id: string;
  available: boolean;
  /** The saved-loadout shape remains the canonical character/build representation. */
  state: SavedLoadout;
  /** Explicitly declared job/rank access for this resident; empty means not captured. */
  jobRanks: Array<{ jobName: string; ranks: string[] }>;
  /** Optional per-stat upper limits. Missing values stay unknown and are not guessed. */
  trainingCaps?: Partial<Record<StatKey, number>>;
  /** User-captured current battle-skill slot capacity. Missing stays unknown. */
  skillSlotCapacity?: number;
};

export type OwnedEquipmentStack = {
  /** Stable equipment-type identity; this is one shared account-wide stack per item type. */
  id: string;
  equipmentName: string;
  ownership: "owned" | "unowned" | "unknown";
  /** Zero for unowned / unknown; owned rows always have at least one copy. */
  quantity: number;
  /** One global upgrade level shared by all copies of this equipment type. */
  currentLevel: number;
};

export type PlayerProfile = {
  schema: typeof PLAYER_PROFILE_SCHEMA;
  updatedAt: number;
  characters: PlayerCharacter[];
  equipment: OwnedEquipmentStack[];
  /** Unlocked skill names. This is ownership data, separate from a character's current skills. */
  ownedSkills: string[];
  valuables: {
    /** Global resident-wide counts already applied; null means the value is not captured. */
    active: Record<string, number | null>;
    /** Inventory remaining, when known. Never inferred from active counts. */
    remaining: Record<string, number | null>;
  };
  permanentState: Record<string, unknown>;
};

export type SolverSettings = {
  targetPolicy: "exact" | "exact-then-nearest";
  inventoryPolicy: "owned-current" | "owned-upgrades" | "theoretical";
  optionDisplay: "hide-infeasible" | "show-disabled";
  maximumSearchNodes: number;
};

export const DEFAULT_SOLVER_SETTINGS: SolverSettings = {
  targetPolicy: "exact-then-nearest",
  inventoryPolicy: "owned-current",
  optionDisplay: "show-disabled",
  maximumSearchNodes: 100_000,
};

export type OptimizerEvidence = {
  libraryName: string;
  librarySchemaVersion: number;
  candidateId: string;
  label: string | null;
  source: string | null;
  family: string | null;
  phase: string;
  meanEarned: number | null;
  highestEarned: number | null;
  sampleCount: number;
  uncertainty: { kind: string; value: number | null };
  raw: Record<string, unknown>;
};

export type OptimizerTargetUnit = {
  key: string;
  name: string;
  kind: "human" | "pet" | "unknown";
  identityStatus: "mapped" | "unmapped";
  jobName: string | null;
  rank: string | null;
  /** Only stats present in the optimizer's recorded summary are populated. */
  exactPoint: Partial<Record<StatKey, number>>;
  /** Raw parameter values, including values without a proven site stat mapping. */
  exactParameters: Record<string, { value: number | null; maximum: number | null }>;
  controllable: Array<{ field: string; value: unknown; source: string }>;
  derived: Array<{ field: string; value: unknown; source: string }>;
  simulatorOnly: Array<{ field: string; value: unknown; source: string }>;
  unmapped: Array<{ field: string; value: unknown; reason: string }>;
  raw: Record<string, unknown>;
};

export type NormalizedOptimizerTarget = {
  schema: typeof NORMALIZED_TARGET_SCHEMA;
  encounterId: number | null;
  encounterIdentity: { indexedId: number | null; scenarioId: number | null; matches: boolean };
  evidence: OptimizerEvidence;
  units: OptimizerTargetUnit[];
  /** Exact measured/synthetic point only. Future measured regions remain null in v1. */
  exactPoint: Record<string, Partial<Record<StatKey, number>>>;
  viabilityRegion: null;
  simulatorOnly: Array<{ field: string; value: unknown; source: string }>;
  unmapped: Array<{ field: string; value: unknown; reason: string }>;
  raw: Record<string, unknown>;
};

export type BuildLock = { field: string; value: unknown };

export type RequiredUpgrade = {
  equipmentName: string;
  inventoryStackId: string | null;
  instanceId?: string | null;
  quantity: number;
  currentLevel: number | null;
  requiredLevel: number;
  addedLevels: number;
  cost: null;
};

export type TranslationStatus =
  | "exact-owned-now"
  | "exact-owned-upgrades"
  | "nearest-owned-now"
  | "nearest-with-upgrades"
  | "theoretical-unowned"
  | "unmapped-or-unproven"
  | "search-incomplete"
  | "runtime-mismatch"
  | "runtime-failed";

export type TranslationResult = {
  characterId: string;
  status: TranslationStatus;
  savedLoadout: SavedLoadout;
  target: Record<string, number>;
  achieved: Record<string, number>;
  deltas: Record<string, number>;
  /** Fast site calculation only. This never establishes a final exact claim. */
  solverExact: boolean;
  /** True only after the authoritative battle preview prepared matching mapped values. */
  exact: boolean;
  runtimeVerification?: { status: "verified" | "mismatch" | "failed"; message: string };
  owned: boolean;
  requiredUpgrades: RequiredUpgrade[];
  equipmentInstances: Record<string, { stackId: string | null; instanceId: string | null; equipmentName: string | null; level: number | null }>;
  unsupportedFields: Array<{ field: string; reason: string }>;
  sourceUnmappedFields: Array<{ field: string; reason: string }>;
  legalityIssues: string[];
  evidence: OptimizerEvidence;
  search: { complete: boolean; nodes: number; reason?: string };
};

export type CandidateBuildOption = {
  encounterId: number;
  candidateId: string;
  evidence: OptimizerEvidence;
  /** Number of player-side units in the recorded candidate, including declared available units. */
  requiredUnitCount: number;
  plannedUnitCount?: number;
  declaredAvailableUnitCount?: number;
  unitPlans?: CoverageUnitPlan[];
  requiredCharacterIds: string[];
  results: TranslationResult[];
  allRequiredUnitsExact: boolean;
  unsupportedRequirements: string[];
};

export type CoverageUnitPlan = {
  unitKey: string;
  name: string;
  status: "planned" | "player-declared-available";
  characterId?: string;
};

export type CoveragePlan = {
  complete: boolean;
  searchNodes: number;
  selectedEncounterIds: number[];
  coveredEncounterIds: number[];
  approximatedEncounterIds: number[];
  uncovered: Array<{ encounterId: number; reason: string }>;
  reusableCharacterIds: string[];
  characterBudget: number;
  plannedUnitCount: number;
  declaredAvailableUnitCount: number;
  choices: CandidateBuildOption[];
  sharedUpgrades: RequiredUpgrade[];
  objective: { exactCovered: number; charactersUsed: number; addedEquipmentLevels: number };
};

export function createEmptyPlayerProfile(): PlayerProfile {
  return {
    schema: PLAYER_PROFILE_SCHEMA,
    updatedAt: Date.now(),
    characters: [],
    equipment: [],
    ownedSkills: [],
    valuables: { active: {}, remaining: {} },
    permanentState: {},
  };
}

export function normalizePlayerProfile(value: unknown): PlayerProfile {
  const fallback = createEmptyPlayerProfile();
  if (!value || typeof value !== "object") return fallback;
  const raw = value as Partial<PlayerProfile>;
  const active: Record<string, number | null> = {};
  const remaining: Record<string, number | null> = {};
  for (const [key, count] of Object.entries(raw.valuables?.active ?? {})) {
    active[key] = count === null ? null : finiteNonNegativeInteger(count);
  }
  for (const [key, count] of Object.entries(raw.valuables?.remaining ?? {})) {
    remaining[key] = count === null ? null : finiteNonNegativeInteger(count);
  }
  return {
    schema: PLAYER_PROFILE_SCHEMA,
    updatedAt: Number.isFinite(raw.updatedAt) ? Number(raw.updatedAt) : Date.now(),
    characters: Array.isArray(raw.characters) ? raw.characters.filter(isPlayerCharacter) : [],
    equipment: normalizeEquipmentRows(raw.equipment),
    ownedSkills: Array.isArray(raw.ownedSkills) ? raw.ownedSkills.filter((item): item is string => typeof item === "string") : [],
    valuables: { active, remaining },
    permanentState: isRecord(raw.permanentState) ? raw.permanentState : {},
  };
}

function finiteNonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPlayerCharacter(value: unknown): value is PlayerCharacter {
  if (!isRecord(value) || typeof value.id !== "string" || !isRecord(value.state)) return false;
  return Array.isArray(value.jobRanks) && value.jobRanks.every((entry) =>
    isRecord(entry) && typeof entry.jobName === "string" && Array.isArray(entry.ranks));
}

function normalizeEquipmentRows(value: unknown): OwnedEquipmentStack[] {
  if (!Array.isArray(value)) return [];
  const rows = new Map<string, OwnedEquipmentStack>();
  for (const entry of value) {
    if (!isRecord(entry) || typeof entry.equipmentName !== "string" || entry.equipmentName.trim() === "") continue;
    const legacy = entry.ownership === undefined;
    const ownership = entry.ownership === "owned" || entry.ownership === "unowned" || entry.ownership === "unknown"
      ? entry.ownership
      : legacy ? "owned" : null;
    if (!ownership) continue;
    const currentLevel = Number.isInteger(entry.currentLevel) ? Math.max(1, Math.min(99, Number(entry.currentLevel))) : 1;
    const legacyLevels = Array.isArray(entry.instanceLevels)
      ? entry.instanceLevels.filter((level): level is number => Number.isInteger(level) && Number(level) >= 1 && Number(level) <= 99)
      : [];
    // Old profiles stored per-copy levels. Their highest recorded value becomes the shared type level.
    const sharedLevel = legacyLevels.length > 0 ? Math.max(currentLevel, ...legacyLevels) : currentLevel;
    const requestedQuantity = Number.isInteger(entry.quantity) ? Math.max(0, Math.min(999, Number(entry.quantity))) : 0;
    const quantity = ownership === "owned" ? Math.max(1, requestedQuantity) : 0;
    const id = typeof entry.id === "string" && entry.id ? entry.id : `equipment:${entry.equipmentName}`;
    const next: OwnedEquipmentStack = {
      id,
      equipmentName: entry.equipmentName,
      ownership,
      quantity,
      currentLevel: sharedLevel,
    };
    const previous = rows.get(entry.equipmentName);
    if (!previous) {
      rows.set(entry.equipmentName, next);
      continue;
    }
    const mergedOwnership = previous.ownership === "owned" || ownership === "owned"
      ? "owned"
      : previous.ownership === "unowned" || ownership === "unowned" ? "unowned" : "unknown";
    rows.set(entry.equipmentName, {
      ...previous,
      ownership: mergedOwnership,
      quantity: mergedOwnership === "owned" ? Math.min(999, previous.quantity + quantity) : 0,
      currentLevel: Math.max(previous.currentLevel, sharedLevel),
    });
  }
  return [...rows.values()];
}
