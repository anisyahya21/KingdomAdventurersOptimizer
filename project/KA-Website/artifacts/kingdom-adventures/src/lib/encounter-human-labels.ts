/**
 * Pure, read-only human labels for the encounter-aware operator view.
 *
 * The operator screen must say what is being tested in words a person recognises ("DPS
 * Dexterity") rather than a canonical path ("ownUnits.0.parameters.19"). This module only READS
 * published snapshot data and reuses the one shared native parameter metadata
 * (`@/game-data/stat-parameter-ids`) instead of defining a second id map.
 *
 * Rules kept honest:
 *  * a role is attached only when the snapshot itself tied that role to the unit (an explicit
 *    `questionFields` row or a `buildDomain.roleIndices` entry). `ownUnits.0` is never assumed
 *    to be the DPS unit.
 *  * an unresolved path falls back to a readable position ("Unit 1 Dexterity"), never a guess.
 *  * an unknown number or spelling is returned as-is rather than invented.
 */
import {
  PARAMETER_NATIVE_NAMES,
  PARAMETER_STAT_KEYS,
  STAT_PARAMETER_IDS,
  canonicalStatKey,
} from "@/game-data/stat-parameter-ids";

const UNIT_PARAMETER_PATH = /^ownUnits\.(\d+)\.parameters\.(\d+)$/;

/** One snapshot field description, the subset this module reads. */
export type EncounterFieldMapping = {
  field?: string | null;
  role?: string | null;
  stat?: string | null;
  label?: string | null;
  unitIndex?: number | null;
  parameterId?: number | null;
};

/** Snapshot-derived role/field context. Nothing is inferred beyond what is supplied here. */
export type EncounterFieldLabelContext = {
  /** Explicit role/stat rows from `presentation.questionFields` or `snapshot.questionFields`. */
  fields?: readonly EncounterFieldMapping[] | null;
  /** `buildDomain.roleIndices`: role name -> source unit index, when the host published it. */
  roleIndices?: Record<string, number | null> | null;
};

export type EncounterHumanFieldLabel = {
  /** Readable label, e.g. "DPS Dexterity" or "Unit 1 Dexterity". */
  label: string;
  /** The role word, present only when the snapshot tied that role to this field. */
  role: string | null;
  /** The stat word, e.g. "Dexterity"; null when no stat could be resolved. */
  stat: string | null;
  /** 0-based unit index parsed from the path, when the path had one. */
  unitIndex: number | null;
  /** Native combat parameter id parsed from the path, when the path had one. */
  parameterId: number | null;
  /** True only when the role came from snapshot data, never from a positional assumption. */
  roleResolved: boolean;
};

/** Optional host-supplied label overrides, so a host can name units or references its own way. */
export type EncounterHumanLabels = {
  fieldLabel?: (path: string) => string | null;
  referenceLabel?: (referenceId: string | null) => string | null;
  policySummary?: (policy: Record<string, unknown>) => string | null;
};

/** A readable word for a verified role key; unknown roles keep their own spelling. */
export function humanRoleWord(role?: string | null): string | null {
  if (typeof role !== "string") return null;
  const key = role.trim().toLowerCase();
  if (key === "") return null;
  if (key === "dps") return "DPS";
  if (key === "healer" || key === "heal") return "Healer";
  if (key === "fodder") return "Fodder";
  return key.charAt(0).toUpperCase() + key.slice(1);
}

/** Native parameter id -> readable stat word, using the shared recovered names. */
export function humanStatNameFromParameterId(id?: number | null): string | null {
  if (typeof id !== "number" || !Number.isInteger(id)) return null;
  const native = PARAMETER_NATIVE_NAMES[id];
  if (native && native !== "UNKNOWN") return native;
  const key = PARAMETER_STAT_KEYS[id];
  return key ? key.toUpperCase() : null;
}

/** Any stat spelling/key -> readable stat word, using the shared canonical metadata. */
export function humanStatName(stat?: string | null): string | null {
  if (typeof stat !== "string" || stat.trim() === "") return null;
  const key = canonicalStatKey(stat);
  const id = (STAT_PARAMETER_IDS as Record<string, number | undefined>)[key];
  if (id !== undefined) {
    const named = humanStatNameFromParameterId(id);
    if (named) return named;
  }
  return key.toUpperCase();
}

/** Parse `ownUnits.<index>.parameters.<id>`; null when the path is not that shape. */
export function parseUnitParameterPath(
  path?: string | null,
): { unitIndex: number; parameterId: number } | null {
  if (typeof path !== "string") return null;
  const match = UNIT_PARAMETER_PATH.exec(path.trim());
  if (!match) return null;
  const unitIndex = Number.parseInt(match[1], 10);
  const parameterId = Number.parseInt(match[2], 10);
  if (!Number.isInteger(unitIndex) || !Number.isInteger(parameterId)) return null;
  return { unitIndex, parameterId };
}

/** Human label for a canonical field path, given whatever role/mapping data the snapshot had. */
export function humanFieldLabel(
  path?: string | null,
  context: EncounterFieldLabelContext = {},
): EncounterHumanFieldLabel | null {
  const parsed = parseUnitParameterPath(path);
  let unitIndex = parsed?.unitIndex ?? null;
  let parameterId = parsed?.parameterId ?? null;
  let role: string | null = null;
  let stat: string | null = null;

  if (typeof path === "string") {
    const trimmed = path.trim();
    const explicit = (context.fields ?? []).find(
      (entry) => entry && typeof entry.field === "string" && entry.field.trim() === trimmed,
    );
    if (explicit) {
      role = humanRoleWord(explicit.role);
      stat = humanStatName(explicit.stat) ?? humanStatNameFromParameterId(explicit.parameterId);
      if (unitIndex === null && typeof explicit.unitIndex === "number") unitIndex = explicit.unitIndex;
      if (parameterId === null && typeof explicit.parameterId === "number") {
        parameterId = explicit.parameterId;
      }
    }
  }

  // A role can still be resolved by the unit the snapshot tied it to, when no field row names it.
  if (!role && unitIndex !== null && context.roleIndices) {
    for (const [roleKey, index] of Object.entries(context.roleIndices)) {
      if (index === unitIndex) {
        role = humanRoleWord(roleKey);
        break;
      }
    }
  }

  const statWord = stat ?? humanStatNameFromParameterId(parameterId);
  if (!statWord && unitIndex === null) return null;
  const prefix = role ?? (unitIndex !== null ? `Unit ${unitIndex + 1}` : null);
  const label = [prefix, statWord].filter((part): part is string => Boolean(part)).join(" ");
  return {
    label: label || statWord || prefix || "",
    role,
    stat: statWord,
    unitIndex,
    parameterId,
    roleResolved: role !== null,
  };
}

/** Boundary/classification words. "Supported-degraded" is a floor miss, not a verdict. */
export function humanClassification(value?: string | null): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  const key = value.trim().toLowerCase();
  if (key === "supported-acceptable" || key === "supported") {
    return "Meets the reference-preservation target";
  }
  if (key === "supported-degraded" || key === "degraded") {
    return "Below the reference-preservation target";
  }
  if (key === "unresolved") return "Still testing - not enough paired tests yet";
  return value;
}

/** Publication status in plain words; the raw value is always available under technical details. */
export function humanPublicationStatus(value?: string | null): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  const key = value.trim().toLowerCase();
  if (key === "withheld") return "Still testing";
  if (key === "published") return "Published";
  if (key === "ready") return "Ready to publish";
  return value;
}

/** "build X" when an id is published, otherwise the honest community-baseline default. */
export function humanReferenceLabel(referenceId?: string | null): string {
  const id = typeof referenceId === "string" ? referenceId.trim() : "";
  return id ? `build ${id}` : "the community baseline";
}

export const DEFAULT_PRESERVATION_PERCENT = 90;

/** The reference-preservation fraction as a whole percent; defaults to the shipped 90%. */
export function preservationPercent(tolerance?: number | null): number {
  if (
    typeof tolerance === "number" &&
    Number.isFinite(tolerance) &&
    tolerance > 0 &&
    tolerance <= 1
  ) {
    return Math.round(tolerance * 100);
  }
  return DEFAULT_PRESERVATION_PERCENT;
}

/** The one allowed wording for the reference-preservation floor. */
export function preservationTargetSentence(tolerance?: number | null): string {
  return `Keep at least ${preservationPercent(tolerance)}% of this reference's average reward`;
}

/** The paired-test unit contract, stated once so pairs and runs are never blurred together. */
export const PAIRED_TEST_EXPLANATION =
  "A paired test compares one reference run and one candidate run on the same encounter. One pair is one comparison; pairs and whole runs are different units and are never added together.";

/** Why more spend is meaningful at all - stateful reward is measured, never predicted. */
export const WHY_SPEND_MORE =
  "Reward, survival and target selection are stateful, so more runs are the only way to measure them. A build below the reference-preservation target is not automatically useless: a build earning 60 where the reference earns 120 can still be worth running for a different goal.";
