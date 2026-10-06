/**
 * Per-slot "Team build" detail for the Strategy Optimiser's investigation panel.
 *
 * The investigation view must show the exact build that produced a record - not a comma-separated
 * ally list and not raw JSON. This module reads a host-owned, `unknown`-typed scenario and returns
 * one row per own-unit slot in the scenario's recorded order, with that slot's weapon, equipment,
 * recorded parameters and every skill's aligned invocation level.
 *
 * Every name is resolved from the canonical catalogs; when a name is not recovered the numeric id is
 * kept and the row is marked unresolved rather than invented. Raw recorded values stay distinct from
 * the runner-prepared effective values shown elsewhere in the page (this module never prepares them).
 */
import { EQUIPMENT_BY_ID, SKILL_BY_ID, renderSkillName, skillActivationStatus } from "@/lib/battle-setup";
import { INVOCATION_LEVELS, PERMANENTLY_ACTIVE_LABEL } from "@/lib/battle-team-draft";
import {
  PARAMETER_NATIVE_NAMES,
  PARAMETER_STAT_KEYS,
  STAT_PARAMETER_IDS,
  type StatKey,
} from "@/game-data/stat-parameter-ids";
import type { SkillActivationStatus } from "@/game-data/skill-job-admission";

/** Where a parameter's display label came from; `id` means no authoritative name was found. */
export type TeamBuildNameSource = "native" | "stat-key" | "id";

export type TeamBuildParameter = {
  /** Numeric native parameter id exactly as recorded. */
  id: number;
  label: string;
  source: TeamBuildNameSource;
  rawValue: number | null;
  rawMax: number | null;
  extraValue: number | null;
  extraMax: number | null;
  trainingLevel: number | null;
};

export type TeamBuildEquipment = {
  id: number | null;
  /** Resolved catalog name, or `null` when no catalog row exists (the id is still shown). */
  name: string | null;
  level: number | null;
  affinity: number | null;
};

export type TeamBuildSkill = {
  /** 1-based position in the recorded skill order. */
  slot: number;
  id: number | null;
  /** Resolved catalog name, or `null` when no catalog row exists (the id is still shown). */
  name: string | null;
  /** The aligned `invocationLevels` value, preserved verbatim; `null` when none was recorded. */
  invocationLevel: number | null;
  /** Recovered `SkillData.flags` classification (active / permanently_active / unknown). */
  status: SkillActivationStatus;
  /** Human trigger label when the level means one; honest text otherwise. */
  triggerLabel: string;
  /** False when this slot has no aligned invocation level (a length mismatch was flagged). */
  paired: boolean;
};

export type TeamBuildSlot = {
  /** 1-based scenario order. */
  slot: number;
  label: string;
  name: string;
  kind: "resident" | "pet" | "unknown";
  weaponId: number | null;
  weaponName: string | null;
  equipment: TeamBuildEquipment[];
  parameters: TeamBuildParameter[];
  skills: TeamBuildSkill[];
  /** Whether the recorded `skills` and `invocationLevels` arrays lined up one-to-one. */
  skillsPaired: boolean;
  skillsRecorded: number;
  invocationLevelsRecorded: number;
  /** Honest notes about missing or unpaired data for this slot. */
  notes: string[];
};

/**
 * Which recorded parameters belong in a combat-first slot view, and which do not.
 *
 * The split is a presentation rule read off the canonical key/id table (this module names canonical
 * keys and takes their ids from `STAT_PARAMETER_IDS`); it is not a second id mapping. It follows the
 * recovered combat readers rather than a guess:
 *
 *   * `tools/recovery/combat_resolution.py` `damage_from_parameters` selects Intelligence (18) for a
 *     magical attack and Attack (13) otherwise, and reads the defender's Defense (14);
 *   * `hit_rate` reads Dexterity (19), Agility (15) and Luck (16); `attack_interval` reads Agility;
 *   * HP (10), MP (11) and Energy/Vigor (12) are the resources a fight spends.
 *
 * No combat reader consults Gathering (20), the unrecovered slot MOV (21) or Love (22), so they stay
 * out of the primary view and remain readable under the slot's raw recorded parameters.
 */
export const COMBAT_PARAMETER_KEYS: readonly StatKey[] = [
  "hp",
  "mp",
  "vig",
  "atk",
  "def",
  "spd",
  "lck",
  "int",
  "dex",
];

/** Recorded parameters with no recovered combat reader: shown only as raw/advanced provenance. */
export const ADVANCED_PARAMETER_KEYS: readonly StatKey[] = ["gth", "mov", "hrt"];

export const COMBAT_PARAMETER_IDS: readonly number[] = COMBAT_PARAMETER_KEYS.map(
  (key) => STAT_PARAMETER_IDS[key],
);

export const ADVANCED_PARAMETER_IDS: readonly number[] = ADVANCED_PARAMETER_KEYS.map(
  (key) => STAT_PARAMETER_IDS[key],
);

/** Whether a recorded parameter id is read by combat resolution. */
export function isCombatParameter(id: number): boolean {
  return COMBAT_PARAMETER_IDS.includes(id);
}

/**
 * `0x7fffffff`: the native "no ceiling" sentinel an unbounded parameter carries. It is not a stat
 * ceiling, so the primary view never prints it as one.
 */
export const UNBOUNDED_PARAMETER_MAX = 2147483647;

/** One slot's recorded parameters split into the combat reading and the raw/advanced remainder. */
export type SlotParameterView = {
  combat: TeamBuildParameter[];
  advanced: TeamBuildParameter[];
  /** True when this slot recorded an Intelligence value, so INT can be shown for a magic attacker. */
  hasIntelligence: boolean;
};

export function slotParameterView(parameters: readonly TeamBuildParameter[]): SlotParameterView {
  const combat: TeamBuildParameter[] = [];
  const advanced: TeamBuildParameter[] = [];
  for (const parameter of parameters) {
    (isCombatParameter(parameter.id) ? combat : advanced).push(parameter);
  }
  return {
    combat,
    advanced,
    hasIntelligence: combat.some((parameter) => parameter.id === STAT_PARAMETER_IDS.int),
  };
}

/** How one recorded raw value reads: no sentinel maximum is ever presented as a ceiling. */
export type ParameterReading = {
  value: number | null;
  /** The recorded maximum, or `null` when the parameter has no real ceiling. */
  max: number | null;
  unbounded: boolean;
  text: string;
};

export function parameterReading(parameter: TeamBuildParameter): ParameterReading {
  const unbounded = parameter.rawMax == null || parameter.rawMax === UNBOUNDED_PARAMETER_MAX;
  const value = parameter.rawValue;
  if (value == null) return { value, max: null, unbounded, text: "unrecorded" };
  if (unbounded) return { value, max: null, unbounded, text: String(value) };
  return { value, max: parameter.rawMax, unbounded, text: `${value}/${parameter.rawMax}` };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * A display label for a native parameter id, resolved from the canonical stat/parameter table rather
 * than a second local mapping. `native` is the recovered name; `stat-key` is the site's canonical
 * short key for the one id (21) whose native name is not recovered; `id` means no name exists.
 */
export function parameterLabel(id: number): { label: string; source: TeamBuildNameSource } {
  const native = PARAMETER_NATIVE_NAMES[id];
  if (native && native !== "UNKNOWN") return { label: native, source: "native" };
  const key = PARAMETER_STAT_KEYS[id];
  if (key) return { label: key.toUpperCase(), source: "stat-key" };
  return { label: `Parameter ${id}`, source: "id" };
}

/** The recovered catalog name for an equipment id, or `null` when the id has no catalog row. */
export function equipmentName(id: number | null): string | null {
  if (id == null) return null;
  return EQUIPMENT_BY_ID.get(id)?.name ?? null;
}

/** The recovered catalog name for a skill id, or `null` when the id has no catalog row. */
export function skillName(id: number | null): string | null {
  if (id == null) return null;
  const entry = SKILL_BY_ID.get(id);
  return entry ? renderSkillName(entry) : null;
}

/**
 * The player-facing meaning of one skill's recorded invocation level. The numeric level is always
 * preserved by the caller; this only says what it means. A permanently-active row has no trigger
 * level to choose, an id with no recovered `SkillData` row cannot be classified, and a level outside
 * 0/1/2 is shown as-is rather than coerced to the native default.
 */
export function skillTrigger(
  id: number | null,
  level: number | null,
): { status: SkillActivationStatus; label: string } {
  if (id == null) return { status: "unknown", label: "skill id not recorded" };
  const status = skillActivationStatus(id);
  if (status === "permanently_active") return { status, label: PERMANENTLY_ACTIVE_LABEL };
  if (status === "unknown") return { status, label: "activation not recovered" };
  const option = INVOCATION_LEVELS.find((entry) => entry.value === level);
  if (option) return { status, label: option.label };
  if (level == null) return { status, label: "invocation level not recorded" };
  return { status, label: `invocation level ${level} (outside 0/1/2)` };
}

/** Skill ids as recorded: a bare number, or an object carrying `skillId`/`id` and a fallback level. */
function readSkillEntries(skills: unknown): Array<{ id: number | null; level: number | null }> {
  if (!Array.isArray(skills)) return [];
  return skills.map((entry) => {
    if (typeof entry === "number" && Number.isFinite(entry)) return { id: entry, level: null };
    if (isRecord(entry)) {
      const id = numberOrNull(entry.skillId) ?? numberOrNull(entry.id);
      const level = numberOrNull(entry.invocationLevel) ?? numberOrNull(entry.level);
      return { id, level };
    }
    return { id: null, level: null };
  });
}

function readEquipment(equipment: unknown): TeamBuildEquipment[] {
  if (!Array.isArray(equipment)) return [];
  return equipment.map((entry) => {
    const record = isRecord(entry) ? entry : {};
    const id = numberOrNull(record.id);
    return {
      id,
      name: equipmentName(id),
      level: numberOrNull(record.level),
      affinity: numberOrNull(record.affinity),
    };
  });
}

function readParameters(parameters: unknown): TeamBuildParameter[] {
  if (!isRecord(parameters)) return [];
  return Object.entries(parameters)
    .map(([key, value]) => {
      const id = Number(key);
      if (!Number.isFinite(id)) return null;
      const record = isRecord(value) ? value : {};
      const { label, source } = parameterLabel(id);
      return {
        id,
        label,
        source,
        rawValue: numberOrNull(record.rawValue),
        rawMax: numberOrNull(record.rawMax),
        extraValue: numberOrNull(record.extraValue),
        extraMax: numberOrNull(record.extraMax),
        trainingLevel: numberOrNull(record.trainingLevel),
      };
    })
    .filter((row): row is TeamBuildParameter => row !== null)
    .sort((left, right) => left.id - right.id);
}

function unitName(unit: Record<string, unknown>, index: number): string {
  const name = typeof unit.name === "string" && unit.name.length > 0 ? unit.name : null;
  if (name) return name;
  const monsterId = numberOrNull(unit.monsterId);
  if (monsterId != null) return `Monster ${monsterId}`;
  return `slot ${index + 1}`;
}

/** One scenario unit -> one slot row. Exported so the check can exercise a single slot directly. */
export function teamBuildSlot(unit: unknown, index: number): TeamBuildSlot {
  const record = isRecord(unit) ? unit : {};
  const skills = readSkillEntries(record.skills);
  const levelsRaw = Array.isArray(record.invocationLevels) ? record.invocationLevels : null;
  const levels = levelsRaw ? levelsRaw.map(numberOrNull) : null;
  const notes: string[] = [];

  const rows: TeamBuildSkill[] = skills.map((entry, position) => {
    const aligned = levels ? levels[position] ?? null : null;
    const invocationLevel = aligned ?? entry.level ?? null;
    const trigger = skillTrigger(entry.id, invocationLevel);
    return {
      slot: position + 1,
      id: entry.id,
      name: skillName(entry.id),
      invocationLevel,
      status: trigger.status,
      triggerLabel: trigger.label,
      paired: levels ? position < levels.length : false,
    };
  });

  const skillsPaired = levels !== null && skills.length === levels.length;
  if (levels === null && skills.length > 0) {
    notes.push("invocationLevels not recorded; each skill's aligned level is unknown.");
  } else if (levels !== null && levels.length > skills.length) {
    notes.push(
      `${levels.length - skills.length} invocation level(s) have no skill: ${levels
        .slice(skills.length)
        .map((value) => (value == null ? "unknown" : value))
        .join(", ")}.`,
    );
  } else if (levels !== null && skills.length > levels.length) {
    notes.push(
      `${skills.length - levels.length} skill(s) have no aligned invocation level: slots ${rows
        .filter((row) => !row.paired)
        .map((row) => row.slot)
        .join(", ")}.`,
    );
  }

  const equipment = readEquipment(record.equipment);
  const weaponId = numberOrNull(record.weaponId);
  const human = record.human;
  return {
    slot: index + 1,
    label: `Slot ${index + 1}`,
    name: unitName(record, index),
    kind: human === false ? "pet" : human === true ? "resident" : "unknown",
    weaponId,
    weaponName: equipmentName(weaponId),
    equipment,
    parameters: readParameters(record.parameters),
    skills: rows,
    skillsPaired,
    skillsRecorded: skills.length,
    invocationLevelsRecorded: levels ? levels.length : 0,
    notes,
  };
}

/**
 * Every own-unit slot of a scenario, in the exact order the scenario recorded. A scenario that is
 * not an object, or carries no `ownUnits` array, yields no slots rather than a guessed party.
 */
export function teamBuildSlots(scenario: unknown): TeamBuildSlot[] {
  if (!isRecord(scenario)) return [];
  const units = scenario.ownUnits;
  if (!Array.isArray(units)) return [];
  return units.map((unit, index) => teamBuildSlot(unit, index));
}

/**
 * The frozen scenario that actually produced a record, resolved once for every investigation target.
 * A pruned record holder is only reachable through the host's `detail.scenario`; a live candidate
 * also returns that same frozen scenario, and only falls back to the candidate's own scenario when
 * the host returned none - so a record holder's build is the build the record was scored on.
 */
export function frozenInvestigationScenario(detailScenario: unknown, candidateScenario: unknown): unknown {
  return detailScenario ?? candidateScenario ?? null;
}

/* ------------------------------------------------------------------ */
/* Fresh-trial batch aggregation                                       */
/* ------------------------------------------------------------------ */

/** One completed run's own reading, as the investigation tables already read a persisted run. */
export type BatchRunReading = {
  outcome: "win" | "loss" | "unresolved" | "unknown";
  /** Chests this run earned: a victory's released count, or a defeat's gate zero. */
  earned: number | null;
  /** Chests a defeat reached as an opportunity; never a yield. */
  potential: number | null;
  seeds: [number, number] | null;
};

/**
 * The part of the host's own bank summary (`_summarize_attempts`) a batch aggregation reads. A null
 * mean stays unknown; nothing is filled in from a maximum.
 */
export type HostBankSummary = {
  samples: number;
  wins: number;
  losses: number;
  noVerdict: number;
  meanEarned: number | null;
  earnedSamples: number;
  meanPotential: number | null;
  potentialSamples: number;
  bestEarned?: number | null;
  bestPotential?: number | null;
  /**
   * The host's own comparability flag: a complete retained bank with no unresolved runs, so two
   * comparable banks share one sample definition.
   */
  comparable?: boolean;
};

/** Why the stored bank could, or could not, be compared with this batch. */
export type BatchComparisonState =
  | "available"
  | "no-stored-baseline"
  | "stored-not-comparable"
  | "batch-incomplete"
  | "no-earned-mean";

export type TrialBatchSummary = {
  /** How many fights the reader asked for in this one batch (may span several chunk calls). */
  requested: number;
  /** How many chunk calls reported back. */
  chunks: number;
  /** Rows the host reported across those chunks. */
  reported: number;
  wins: number;
  losses: number;
  noVerdict: number;
  meanEarned: number | null;
  earnedSamples: number;
  bestEarned: number | null;
  /** Lowest earned reading among this batch's resolved runs; null when none was recorded. */
  lowEarned: number | null;
  /** `bestEarned - lowEarned`, the batch's own spread; null when either end is unknown. */
  spreadEarned: number | null;
  meanPotential: number | null;
  potentialSamples: number;
  bestPotential: number | null;
  comparison: {
    state: BatchComparisonState;
    storedMean: number | null;
    storedSamples: number;
    /** `meanEarned - storedMean`; only set when the comparison is available. */
    delta: number | null;
    note: string;
  };
};

function meanOf(total: number, samples: number): number | null {
  return samples > 0 ? total / samples : null;
}

function sumBest(
  left: number | null | undefined,
  right: number | null | undefined,
): number | null {
  if (left == null) return right ?? null;
  if (right == null) return left;
  return Math.max(left, right);
}

function compareWithStored(
  requested: number,
  reported: number,
  meanEarned: number | null,
  stored: HostBankSummary | null | undefined,
): TrialBatchSummary["comparison"] {
  if (!stored) {
    return {
      state: "no-stored-baseline",
      storedMean: null,
      storedSamples: 0,
      delta: null,
      note: "This build has no stored bank, so there is nothing to compare the batch with yet.",
    };
  }
  const storedMean = stored.meanEarned;
  const base = { storedMean, storedSamples: stored.earnedSamples, delta: null };
  if (stored.comparable !== true) {
    return {
      ...base,
      state: "stored-not-comparable",
      note: "The stored bank is not a complete comparable sample (attempts rolled off, or runs carried no verdict), so its mean is not compared with this batch.",
    };
  }
  if (reported < requested) {
    return {
      ...base,
      state: "batch-incomplete",
      note: `Only ${reported} of the ${requested} requested fights are in this batch, so it is not compared with the stored bank.`,
    };
  }
  if (meanEarned == null || storedMean == null) {
    return {
      ...base,
      state: "no-earned-mean",
      note: "Neither bank has a measured mean earned over resolved runs, so there is nothing to compare.",
    };
  }
  const delta = meanEarned - storedMean;
  return {
    state: "available",
    storedMean,
    storedSamples: stored.earnedSamples,
    delta,
    note: `Same definition on both sides: mean chests earned per resolved run, defeats counted as zero, over the stored bank's ${stored.earnedSamples} retained resolved run(s).`,
  };
}

/**
 * Aggregate one entire requested batch of fresh fights.
 *
 * The host answers a batch in chunk calls of at most eight and publishes its own `batchSummary` for
 * each chunk, so those summaries are merged here rather than the card showing only the cumulative
 * trial total. When the host published no summary (an older host build), the batch rows are tallied
 * with the same rule the host uses: a defeat contributes the gate's zero to earned and its own
 * opportunity to potential, a win contributes its released count, and any other outcome is a
 * no-verdict row kept out of both means.
 */
export function summarizeTrialBatch(input: {
  requested: number;
  chunkSummaries: readonly (HostBankSummary | null | undefined)[];
  readings: readonly BatchRunReading[];
  storedSummary?: HostBankSummary | null;
}): TrialBatchSummary {
  const requested = Math.max(0, Math.trunc(input.requested));
  const summaries = input.chunkSummaries.filter(
    (summary): summary is HostBankSummary => summary != null,
  );

  let chunks = 0;
  let reported = 0;
  let wins = 0;
  let losses = 0;
  let noVerdict = 0;
  let earnedSamples = 0;
  let earnedTotal = 0;
  let bestEarned: number | null = null;
  let potentialSamples = 0;
  let potentialTotal = 0;
  let bestPotential: number | null = null;

  if (summaries.length > 0) {
    chunks = summaries.length;
    for (const summary of summaries) {
      reported += summary.samples;
      wins += summary.wins;
      losses += summary.losses;
      noVerdict += summary.noVerdict;
      if (summary.meanEarned != null) {
        earnedSamples += summary.earnedSamples;
        earnedTotal += summary.meanEarned * summary.earnedSamples;
      }
      if (summary.meanPotential != null) {
        potentialSamples += summary.potentialSamples;
        potentialTotal += summary.meanPotential * summary.potentialSamples;
      }
      bestEarned = sumBest(bestEarned, summary.bestEarned);
      bestPotential = sumBest(bestPotential, summary.bestPotential);
    }
  } else {
    for (const reading of input.readings) {
      reported += 1;
      if (reading.outcome === "loss") {
        losses += 1;
        earnedSamples += 1;
        earnedTotal += reading.earned ?? 0;
        if (reading.potential != null) {
          potentialSamples += 1;
          potentialTotal += reading.potential;
          bestPotential = sumBest(bestPotential, reading.potential);
        }
      } else if (reading.outcome === "win") {
        wins += 1;
        if (reading.earned != null) {
          earnedSamples += 1;
          earnedTotal += reading.earned;
          bestEarned = sumBest(bestEarned, reading.earned);
        }
      } else {
        noVerdict += 1;
      }
    }
  }

  const meanEarned = meanOf(earnedTotal, earnedSamples);
  /*
   * The batch's own earned list, read exactly as the host reads it: a defeat contributes the gate's
   * zero, a win its recorded count, and any other outcome contributes nothing. Only this list feeds
   * the low reading and the spread; the mean and its sample count stay the host's own numbers.
   */
  const earnedReadings: number[] = [];
  for (const reading of input.readings) {
    if (reading.outcome === "loss") earnedReadings.push(reading.earned ?? 0);
    else if (reading.outcome === "win" && reading.earned != null) earnedReadings.push(reading.earned);
  }
  const lowEarned = earnedReadings.length > 0 ? Math.min(...earnedReadings) : null;
  const spreadEarned =
    bestEarned != null && lowEarned != null ? bestEarned - lowEarned : null;

  return {
    requested,
    chunks,
    reported,
    wins,
    losses,
    noVerdict,
    meanEarned,
    earnedSamples,
    bestEarned,
    lowEarned,
    spreadEarned,
    meanPotential: meanOf(potentialTotal, potentialSamples),
    potentialSamples,
    bestPotential,
    comparison: compareWithStored(requested, reported, meanEarned, input.storedSummary),
  };
}
