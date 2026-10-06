/**
 * Pure local helpers for the player operating-region read model.
 *
 * Nothing here performs I/O, sends a command, runs a battle or fetches on a poll. The queries are
 * LOCAL filters over an already-loaded summary: they never interpolate between measured points,
 * never snap a typed value to a nearby one, never assume higher is better and never pool evidence
 * across candidates, regions or policy groups. An unsampled value resolves to an explicit unknown
 * plus the nearby MEASURED options.
 *
 * The shapes below mirror `work/encounter-redesign/player-region-contract.md` exactly: archetypes
 * carry `regions` (not `tradeoffs`) and an OBJECT `fixedRequirements`; points carry the canonical
 * `stats` vector keyed `<role>.<stat>`, the exact `classification`, the verbatim `evidenceStatus`,
 * and the sample/total/unknown counts. A query reads `point.stats[field]` for any available
 * `<role>.<stat>` key, never a hypothetical `knobField`.
 */
import type {
  PlayerRegionArchetype,
  PlayerRegionCoverage,
  PlayerRegionFixedRequirements,
  PlayerRegionFormation,
  PlayerRegionMeanSpan,
  PlayerRegionPlacement,
  PlayerRegionPoint,
  PlayerRegionQuery,
  PlayerRegionQueryMatch,
  PlayerRegionQueryResult,
  PlayerRegionRewardOption,
  PlayerRegionRewardResult,
  PlayerRegionScope,
  PlayerRegionSkillEntry,
  PlayerRegionSparsePoint,
  PlayerRegionStatVector,
  PlayerRegionsSummary,
} from "@/types/player-regions";

/* ------------------------------------------------------------------ */
/* Guards                                                              */
/* ------------------------------------------------------------------ */

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function asStringArray(value: unknown): string[] {
  return asArray(value)
    .map((entry) => asString(entry))
    .filter((entry): entry is string => entry !== null);
}

/**
 * An id the backend may publish as either a string or a number (skill ids, weapon ids). Rendered
 * verbatim; a numeric `0` is a real id, so it is kept rather than dropped as falsy/absent.
 */
function asIdString(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function asIdArray(value: unknown): string[] {
  return asArray(value)
    .map((entry) => asIdString(entry))
    .filter((entry): entry is string => entry !== null);
}

/** Keep only finite numbers; a key the backend did not publish stays absent (unknown, never 0). */
function asStatVector(value: unknown): PlayerRegionStatVector {
  const record = asRecord(value);
  const out: PlayerRegionStatVector = {};
  if (!record) return out;
  for (const [key, entry] of Object.entries(record)) {
    const number = asNumber(entry);
    if (number !== null) out[key] = number;
  }
  return out;
}

function asNumberMap(value: unknown): Record<string, number> | null {
  const record = asRecord(value);
  if (!record) return null;
  const out: Record<string, number> = {};
  for (const [key, entry] of Object.entries(record)) {
    const number = asNumber(entry);
    if (number !== null) out[key] = number;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/* ------------------------------------------------------------------ */
/* Normalisation (authoritative contract only)                         */
/* ------------------------------------------------------------------ */

function normalizePlacement(value: unknown): PlayerRegionPlacement | null {
  const record = asRecord(value);
  if (!record) return null;
  return {
    grid: asNumber(record.grid),
    name: asString(record.name),
  };
}

function normalizeFormation(value: unknown): PlayerRegionFormation | null {
  const record = asRecord(value);
  if (!record) return null;
  const placedRaw = record.placed;
  const placed = Array.isArray(placedRaw)
    ? placedRaw.map(normalizePlacement).filter((entry): entry is PlayerRegionPlacement => entry !== null)
    : null;
  return {
    roster: asStringArray(record.roster),
    placed,
    placementStatus: asString(record.placementStatus),
  };
}

function normalizeSkillEntry(value: unknown): PlayerRegionSkillEntry | null {
  const record = asRecord(value);
  if (!record) return null;
  return {
    unit: asString(record.unit),
    // The real bridge publishes skills as numeric ids (e.g. 26, 110) and weaponId as a number:
    // stringify without dropping anything, so a fixed requirement is never rendered as empty.
    skills: asIdArray(record.skills),
    invocationLevels: record.invocationLevels ?? null,
    weaponId: asIdString(record.weaponId),
  };
}

function normalizeFixedRequirements(value: unknown): PlayerRegionFixedRequirements | null {
  const record = asRecord(value);
  if (!record) return null;
  const skillsRaw = record.skills;
  return {
    formation: normalizeFormation(record.formation),
    skills: Array.isArray(skillsRaw)
      ? skillsRaw.map(normalizeSkillEntry).filter((entry): entry is PlayerRegionSkillEntry => entry !== null)
      : null,
    policy: asString(record.policy),
    other: asRecord(record.other),
  };
}

function normalizePoint(value: unknown): PlayerRegionPoint | null {
  const record = asRecord(value);
  if (!record) return null;
  const point: PlayerRegionPoint = {
    candidateId: asString(record.candidateId),
    label: asString(record.label),
    stats: asStatVector(record.stats),
    meanEarned: asNumber(record.meanEarned),
    partialMean: asNumber(record.partialMean),
    samples: asNumber(record.samples),
    total: asNumber(record.total),
    unknownCount: asNumber(record.unknownCount),
    uncertainty: record.uncertainty ?? null,
    classification: asString(record.classification),
    evidenceStatus: asString(record.evidenceStatus),
    context: asString(record.context),
    reachability: asString(record.reachability),
    resourcePolicy: record.resourcePolicy ?? null,
  };
  if ("median" in record) point.median = asNumber(record.median);
  if ("quantiles" in record) point.quantiles = asNumberMap(record.quantiles);
  if ("thresholds" in record) point.thresholds = record.thresholds ?? null;
  if ("referenceId" in record) point.referenceId = asString(record.referenceId);
  if ("referenceMeanEarned" in record) point.referenceMeanEarned = asNumber(record.referenceMeanEarned);
  if ("tolerance" in record) point.tolerance = asNumber(record.tolerance);
  if ("changedFields" in record) point.changedFields = asStringArray(record.changedFields);
  if ("fixedFields" in record) point.fixedFields = record.fixedFields ?? null;
  return point;
}

function normalizeRegion(value: unknown, index: number): PlayerRegionArchetype["regions"][number] | null {
  const record = asRecord(value);
  if (!record) return null;
  return {
    id: asString(record.id) ?? `region-${index}`,
    label: asString(record.label),
    kind: asString(record.kind),
    conditions: asStringArray(record.conditions),
    points: asArray(record.points)
      .map(normalizePoint)
      .filter((entry): entry is PlayerRegionPoint => entry !== null),
    gaps: asStringArray(record.gaps),
    lowImpact: asStringArray(record.lowImpact),
    support: asStringArray(record.support),
    continuousCoverage: asBoolean(record.continuousCoverage),
    safeCartesianProduct: asBoolean(record.safeCartesianProduct),
  };
}

function normalizeArchetype(value: unknown, index: number): PlayerRegionArchetype | null {
  const record = asRecord(value);
  if (!record) return null;
  return {
    id: asString(record.id) ?? `archetype-${index}`,
    label: asString(record.label),
    description: asString(record.description),
    fixedRequirements: normalizeFixedRequirements(record.fixedRequirements),
    context: asString(record.context),
    reachability: asString(record.reachability),
    regions: asArray(record.regions)
      .map(normalizeRegion)
      .filter((entry): entry is PlayerRegionArchetype["regions"][number] => entry !== null),
    unknowns: asStringArray(record.unknowns),
  };
}

function normalizeScope(value: unknown): PlayerRegionScope | null {
  const record = asRecord(value);
  if (!record) return null;
  const encounterId = record.encounterId;
  return {
    encounterId:
      typeof encounterId === "number" || typeof encounterId === "string" ? encounterId : null,
    referenceId: asString(record.referenceId),
    mode: asString(record.mode),
  };
}

function normalizeCoverage(value: unknown): PlayerRegionCoverage | null {
  const record = asRecord(value);
  if (!record) return null;
  return {
    candidateLimit: asNumber(record.candidateLimit),
    availableCandidates: asNumber(record.availableCandidates),
    includedCandidates: asNumber(record.includedCandidates),
    truncated: asBoolean(record.truncated),
  };
}

/**
 * Normalise whatever the bridge returned. The authoritative reply is the summary object itself; a
 * `{ summary }`, `{ ok, summary }` or `{ result }` envelope is also accepted (and is exercised by
 * the fixture check) so a thin host wrapper cannot render as empty.
 */
export function normalizePlayerRegionsSummary(raw: unknown): PlayerRegionsSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const direct = "archetypes" in record || "status" in record;
  const inner = direct
    ? record
    : asRecord(record.summary) ?? asRecord(record.result) ?? record;
  const version = inner.version;
  return {
    version:
      typeof version === "string" || typeof version === "number" ? String(version) : null,
    synthesisRevision: asString(inner.synthesisRevision),
    sourceRevision: asString(inner.sourceRevision),
    status: asString(inner.status),
    reason: asString(inner.reason),
    scope: normalizeScope(inner.scope),
    archetypes: asArray(inner.archetypes)
      .map(normalizeArchetype)
      .filter((entry): entry is PlayerRegionArchetype => entry !== null),
    coverage: normalizeCoverage(inner.coverage),
    limitations: asStringArray(inner.limitations),
  };
}

/* ------------------------------------------------------------------ */
/* Labels + field discovery                                            */
/* ------------------------------------------------------------------ */

/**
 * Plain-English names for the standard effective-stat tokens the bridge uses. These are DISPLAY
 * vocabulary for tokens already published by the backend (attack/defense/...), not a re-derived game
 * fact; an unknown token is rendered verbatim so nothing is invented.
 */
const STAT_TOKEN_LABELS: Record<string, string> = {
  atk: "Attack",
  def: "Defense",
  dex: "Dexterity",
  hp: "HP",
  mp: "MP",
  spd: "Speed",
  lck: "Luck",
};

/**
 * `dps.dex` -> `DPS Dexterity`, `healer.hp` -> `HEALER HP`. The role stays an uppercase token; a
 * known stat token gets its plain-English name; any other token is kept verbatim.
 */
export function statFieldLabel(field: string | null | undefined): string {
  if (!field) return "unavailable";
  return field
    .split(".")
    .map((part, index) => {
      const mapped = STAT_TOKEN_LABELS[part.toLowerCase()];
      if (mapped) return mapped;
      return index === 0 ? part.toUpperCase() : part;
    })
    .join(" ");
}

/** Compact numeric rendering for verbatim measurements (no currency/unit is invented here). */
function formatMeasure(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

/** A retention fraction (e.g. 0.9) rendered as a percentage; the percentage is computed, not assumed. */
function formatPercent(fraction: number): string {
  return `${formatMeasure(fraction * 100)}%`;
}

/** Every `<role>.<stat>` key any loaded point actually publishes, sorted. */
export function availableStatFields(summary: PlayerRegionsSummary | null | undefined): string[] {
  const fields = new Set<string>();
  for (const archetype of summary?.archetypes ?? []) {
    for (const region of archetype.regions) {
      for (const point of region.points) {
        for (const key of Object.keys(point.stats)) fields.add(key);
      }
    }
  }
  return [...fields].sort((left, right) => left.localeCompare(right));
}

/** Stat keys the point does not publish relative to the summary union (unknown, not zero). */
export function pointMissingStats(point: PlayerRegionPoint, allFields: string[]): string[] {
  return allFields.filter((field) => !(field in point.stats));
}

/**
 * The OBSERVED span of a field across every loaded point: the smallest and largest measured value
 * actually published on `field`. This is an observed range only - it is NOT a safe/viable range.
 * Values between or beyond these points are untested and stay unknown. Returns null when no point
 * publishes a value on `field`.
 */
export type PlayerRegionFieldSpan = {
  min: number;
  max: number;
  /** How many measured points publish the field (>= 1 when a span exists). */
  count: number;
};

export function observedFieldSpan(
  summary: PlayerRegionsSummary | null | undefined,
  field: string | null | undefined,
): PlayerRegionFieldSpan | null {
  if (!field) return null;
  const values: number[] = [];
  for (const flat of collectMeasuredPoints(summary)) {
    const value = measuredValue(flat.point, field);
    if (value !== null) values.push(value);
  }
  if (values.length === 0) return null;
  return { min: Math.min(...values), max: Math.max(...values), count: values.length };
}

/**
 * The reference band a point is compared against: `referenceMeanEarned * tolerance` when the backend
 * publishes BOTH values. This computes from the backend's own two numbers; it never assumes a fixed
 * percentage. Returns null when either input is missing.
 */
export function referenceRequirementFor(point: PlayerRegionPoint): number | null {
  const reference = point.referenceMeanEarned ?? null;
  const tolerance = point.tolerance ?? null;
  if (reference === null || tolerance === null) return null;
  if (!Number.isFinite(reference) || !Number.isFinite(tolerance)) return null;
  return reference * tolerance;
}

/**
 * A secondary, descriptive line for the reference retention band, e.g. "Below <tolerance percent>
 * of reference <mean>", where BOTH numbers come from the point (its reported tolerance and its own
 * reference mean). The percentage is always the backend's own tolerance - no percentage is
 * hard-coded here. Returns null when the band cannot be computed, so nothing is claimed. This is an
 * observational comparison, not a guarantee.
 */
export function referenceRetentionText(option: {
  tolerance: number | null;
  referenceMeanEarned: number | null;
  referenceRequirement: number | null;
  meetsReference: boolean | null;
}): string | null {
  const reference = option.referenceMeanEarned;
  const requirement = option.referenceRequirement;
  const meets = option.meetsReference;
  if (reference === null || requirement === null || meets === null) return null;
  const percent = option.tolerance !== null ? `${formatPercent(option.tolerance)} of ` : "";
  const band = `${percent}reference ${formatMeasure(reference)}`;
  return meets ? `At or above ${band}` : `Below ${band}`;
}

/**
 * The OBSERVED span of measured reward means across every loaded point. Like the stat span it bounds
 * the target slider by known measurements only - it is NOT a safe/viable reward range. Null when no
 * point publishes a measured mean.
 */
export function observedMeanSpan(
  summary: PlayerRegionsSummary | null | undefined,
): PlayerRegionMeanSpan | null {
  const values: number[] = [];
  for (const flat of collectMeasuredPoints(summary)) {
    const mean = flat.point.meanEarned;
    if (typeof mean === "number" && Number.isFinite(mean)) values.push(mean);
  }
  if (values.length === 0) return null;
  return { min: Math.min(...values), max: Math.max(...values), count: values.length };
}

/** One rendered `fixedRequirements.other` row (key + verbatim-ish text), preserving object values. */
export type PlayerRegionOtherRow = { key: string; text: string };

/** A generic, non-lossy text rendering: an object/array is shown, never silently dropped. */
function looseValue(value: unknown): string {
  if (value === null || value === undefined) return "unknown";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "unknown";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.length ? value.map(looseValue).join(", ") : "none";
  if (typeof value === "object") {
    const parts = Object.entries(value as Record<string, unknown>).map(
      ([key, entry]) => `${key} ${looseValue(entry)}`,
    );
    return parts.length ? parts.join(" · ") : "none";
  }
  return String(value);
}

/**
 * Flatten `fixedRequirements.other` into ordered rows for rendering. Object values (e.g. the
 * `startProfile` scenario block) are rendered rather than dropped, so a fixed requirement is never
 * silently lost.
 */
export function describeFixedRequirementsOther(
  other: Record<string, unknown> | null | undefined,
): PlayerRegionOtherRow[] {
  if (!other) return [];
  const known = ["context", "structuralKey", "encounterId", "defeatCount", "tickLimit", "startProfile"];
  const rows: PlayerRegionOtherRow[] = [];
  for (const key of known) {
    if (!(key in other)) continue;
    const text = looseValue(other[key]);
    rows.push({ key, text: key === "structuralKey" && text.length > 16 ? `${text.slice(0, 16)}…` : text });
  }
  if (other.revisions != null) rows.push({ key: "revisions", text: looseValue(other.revisions) });
  for (const key of Object.keys(other)) {
    if (known.includes(key) || key === "revisions") continue;
    rows.push({ key, text: looseValue(other[key]) });
  }
  return rows;
}

/* ------------------------------------------------------------------ */
/* Filter input parsing (blank is allowed while editing)               */
/* ------------------------------------------------------------------ */

/** A blank box is `null` (no constraint); a non-numeric box is also `null` but flagged invalid. */
export function parseFilterInput(raw: string): number | null {
  const text = raw.trim();
  if (text === "") return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export function filterInputInvalid(raw: string): boolean {
  const text = raw.trim();
  return text !== "" && !Number.isFinite(Number(text));
}

/* ------------------------------------------------------------------ */
/* Flattening                                                          */
/* ------------------------------------------------------------------ */

export type PlayerRegionFlatPoint = {
  archetype: PlayerRegionArchetype;
  region: PlayerRegionArchetype["regions"][number];
  point: PlayerRegionPoint;
};

/** Flatten every point in the summary in wire order, tagged with its archetype and region. */
export function collectMeasuredPoints(
  summary: PlayerRegionsSummary | null | undefined,
): PlayerRegionFlatPoint[] {
  const out: PlayerRegionFlatPoint[] = [];
  for (const archetype of summary?.archetypes ?? []) {
    for (const region of archetype.regions) {
      for (const point of region.points) out.push({ archetype, region, point });
    }
  }
  return out;
}

function measuredValue(point: PlayerRegionPoint, field: string): number | null {
  const raw = point.stats[field];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function toMatch(
  flat: PlayerRegionFlatPoint,
  value: number,
  query: PlayerRegionQuery,
  allFields: string[],
): PlayerRegionQueryMatch {
  const point = flat.point;
  const meanEarned = point.meanEarned;
  const referenceRequirement = referenceRequirementFor(point);
  const meetsReference =
    referenceRequirement !== null && meanEarned !== null ? meanEarned >= referenceRequirement : null;
  let meetsMeanTarget: boolean | null = null;
  if (query.meanTarget !== null && meanEarned !== null) {
    meetsMeanTarget = meanEarned >= query.meanTarget;
  }
  return {
    archetypeId: flat.archetype.id,
    archetypeLabel: flat.archetype.label,
    archetypePolicy: flat.archetype.fixedRequirements?.policy ?? null,
    archetypeContext: flat.archetype.context,
    regionId: flat.region.id,
    regionLabel: flat.region.label,
    regionKind: flat.region.kind,
    value,
    classification: point.classification,
    evidenceStatus: point.evidenceStatus,
    meanEarned,
    partialMean: point.partialMean,
    rewardUnresolved: meanEarned === null,
    samples: point.samples,
    total: point.total,
    unknownCount: point.unknownCount,
    hasUnknown: typeof point.unknownCount === "number" && point.unknownCount > 0,
    uncertainty: point.uncertainty,
    thresholds: point.thresholds ?? null,
    median: point.median ?? null,
    candidateId: point.candidateId,
    referenceId: point.referenceId ?? null,
    referenceMeanEarned: point.referenceMeanEarned ?? null,
    tolerance: point.tolerance ?? null,
    referenceRequirement,
    meetsReference,
    changedFields: point.changedFields ?? [],
    fixedFields: point.fixedFields ?? null,
    context: point.context,
    reachability: point.reachability,
    meetsMeanTarget,
    vectorCompleteForField: true,
    missingStats: pointMissingStats(point, allFields),
    point,
  };
}

function toSparse(flat: PlayerRegionFlatPoint, allFields: string[]): PlayerRegionSparsePoint {
  return {
    archetypeId: flat.archetype.id,
    archetypeLabel: flat.archetype.label,
    regionId: flat.region.id,
    regionLabel: flat.region.label,
    regionKind: flat.region.kind,
    candidateId: flat.point.candidateId,
    classification: flat.point.classification,
    evidenceStatus: flat.point.evidenceStatus,
    meanEarned: flat.point.meanEarned,
    missingStats: pointMissingStats(flat.point, allFields),
    point: flat.point,
  };
}

/**
 * Flatten one whole candidate for the reward-target view. Unlike a stat-query match this needs no
 * stat field: it carries the measured mean, the reference band and the resolved-observation counts.
 * A mean is never assumed; `meetsMeanTarget` stays null when the mean is unknown.
 */
function toRewardOption(flat: PlayerRegionFlatPoint, meanTarget: number | null): PlayerRegionRewardOption {
  const point = flat.point;
  const meanEarned = point.meanEarned ?? null;
  const referenceRequirement = referenceRequirementFor(point);
  const meetsReference =
    referenceRequirement !== null && meanEarned !== null ? meanEarned >= referenceRequirement : null;
  let meetsMeanTarget: boolean | null = null;
  if (meanTarget !== null && meanEarned !== null) meetsMeanTarget = meanEarned >= meanTarget;
  return {
    archetypeId: flat.archetype.id,
    archetypeLabel: flat.archetype.label,
    archetypePolicy: flat.archetype.fixedRequirements?.policy ?? null,
    archetypeContext: flat.archetype.context,
    regionId: flat.region.id,
    regionLabel: flat.region.label,
    regionKind: flat.region.kind,
    candidateId: point.candidateId,
    classification: point.classification,
    evidenceStatus: point.evidenceStatus,
    meanEarned,
    partialMean: point.partialMean ?? null,
    rewardUnresolved: meanEarned === null,
    samples: point.samples ?? null,
    total: point.total ?? null,
    unknownCount: point.unknownCount ?? null,
    hasUnknown: typeof point.unknownCount === "number" && point.unknownCount > 0,
    uncertainty: point.uncertainty ?? null,
    thresholds: point.thresholds ?? null,
    median: point.median ?? null,
    referenceId: point.referenceId ?? null,
    referenceMeanEarned: point.referenceMeanEarned ?? null,
    tolerance: point.tolerance ?? null,
    referenceRequirement,
    meetsReference,
    changedFields: point.changedFields ?? [],
    fixedFields: point.fixedFields ?? null,
    context: point.context,
    reachability: point.reachability,
    meetsMeanTarget,
    point,
  };
}

/* ------------------------------------------------------------------ */
/* Reward-target organisation                                          */
/* ------------------------------------------------------------------ */

/**
 * Organise every measured candidate by an optional reward target. This only GROUPS the same measured
 * options - it never discards one, never interpolates a mean and never pools across candidates,
 * regions or policy groups. A censored/unknown mean is kept in `unknown` and is never counted as
 * below the target. Pass `null` to list every mean-known option without a target.
 */
export function organizeByMeanTarget(
  summary: PlayerRegionsSummary | null | undefined,
  meanTarget: number | null,
): PlayerRegionRewardResult {
  const options = collectMeasuredPoints(summary).map((flat) => toRewardOption(flat, meanTarget));
  const meets: PlayerRegionRewardOption[] = [];
  const below: PlayerRegionRewardOption[] = [];
  const unknown: PlayerRegionRewardOption[] = [];
  for (const option of options) {
    if (option.meanEarned === null) unknown.push(option);
    else if (meanTarget === null || option.meanEarned >= meanTarget) meets.push(option);
    else below.push(option);
  }
  return {
    target: meanTarget,
    meets,
    below,
    unknown,
    retainedBelowTarget: below.length,
    unknownMean: unknown.length,
    withUnknownObservations: options.filter((option) => option.hasUnknown).length,
  };
}

/* ------------------------------------------------------------------ */
/* Query                                                               */
/* ------------------------------------------------------------------ */

/**
 * Resolve a local query against the loaded summary, reading `point.stats[field]`.
 *
 * - `exact`: only points whose measured value EQUALS the typed value. No snapping and no
 *   interpolation - an unsampled value yields explicit unknown plus the nearby measured values.
 * - `range`: measured points within the inclusive bounds.
 * - `all`: every measured point on the queried field.
 *
 * Points that publish no value on the field are returned separately as `sparse` so a missing
 * measurement is surfaced as unknown, never silently dropped. The optional mean target only sets
 * `meetsMeanTarget`; it never removes a lower or unresolved option.
 */
export function queryPlayerRegions(
  summary: PlayerRegionsSummary | null | undefined,
  query: PlayerRegionQuery,
): PlayerRegionQueryResult {
  const field = query.field;
  const all = collectMeasuredPoints(summary);
  if (!summary || !field) {
    return {
      mode: "no-field",
      field,
      matches: [],
      sparse: [],
      unknown: true,
      unknownReason: summary
        ? "The summary publishes no usable <role>.<stat> field to query."
        : "No summary is loaded.",
      nearbyMeasured: [],
      retainedBelowTarget: 0,
      matchesWithUnknown: 0,
    };
  }

  const allFields = availableStatFields(summary);
  const measured: Array<{ flat: PlayerRegionFlatPoint; value: number }> = [];
  const sparse: PlayerRegionSparsePoint[] = [];
  for (const flat of all) {
    const value = measuredValue(flat.point, field);
    if (value === null) sparse.push(toSparse(flat, allFields));
    else measured.push({ flat, value });
  }
  const nearbyMeasured = [...new Set(measured.map((entry) => entry.value))].sort(
    (left, right) => left - right,
  );

  let mode: PlayerRegionQueryResult["mode"];
  let selected: Array<{ flat: PlayerRegionFlatPoint; value: number }>;
  if (query.exact !== null) {
    mode = "exact";
    selected = measured.filter((entry) => entry.value === query.exact);
  } else if (query.low !== null || query.high !== null) {
    mode = "range";
    selected = measured.filter((entry) => {
      if (query.low !== null && entry.value < query.low) return false;
      if (query.high !== null && entry.value > query.high) return false;
      return true;
    });
  } else {
    mode = "all";
    selected = measured;
  }

  const matches = selected.map((entry) => toMatch(entry.flat, entry.value, query, allFields));
  const unknown = matches.length === 0;
  let unknownReason: string | null = null;
  if (unknown) {
    const label = statFieldLabel(field);
    if (mode === "exact") {
      unknownReason =
        nearbyMeasured.length > 0
          ? `No measured point on ${label} equals ${query.exact}. Nearby measured values are listed; nothing is interpolated or snapped.`
          : `No point publishes a measured ${label} value at all.`;
    } else if (mode === "range") {
      unknownReason = `No measured point on ${label} falls inside the typed range.`;
    } else {
      unknownReason = `No point publishes a measured ${label} value at all.`;
    }
  }

  return {
    mode,
    field,
    matches,
    sparse,
    unknown,
    unknownReason,
    nearbyMeasured,
    retainedBelowTarget: matches.filter((match) => match.meetsMeanTarget === false).length,
    matchesWithUnknown: matches.filter((match) => match.hasUnknown).length,
  };
}

/** Serialise the read model exactly as loaded; the export is the read model, not a re-derivation. */
export function exportPlayerRegionsReadModel(summary: PlayerRegionsSummary | null): string {
  return JSON.stringify(summary ?? null, null, 2);
}
