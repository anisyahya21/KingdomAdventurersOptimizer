/**
 * Player operating-region read model - FRONTEND TYPES ONLY.
 *
 * These types mirror the AUTHORITATIVE contract
 * (`work/encounter-redesign/player-region-contract.md`, produced by
 * `Bridge.encounter_player_regions()` / `strategy_player_regions.py`) exactly:
 *
 *  - Root: version, synthesisRevision, sourceRevision, status, reason?, scope,
 *    archetypes, coverage, limitations.
 *  - Archetype: id, label, description, fixedRequirements (an OBJECT
 *    {formation, skills, policy, other}), context, reachability, regions, unknowns.
 *  - Region: id, label, kind, conditions, points, gaps, lowImpact, support,
 *    continuousCoverage, safeCartesianProduct.
 *  - Point: candidateId, label, stats, meanEarned, partialMean, samples, total,
 *    unknownCount, uncertainty, median?, quantiles?, thresholds?, classification,
 *    evidenceStatus, referenceId?, changedFields?, fixedFields?, context,
 *    reachability, resourcePolicy.
 *
 * `stats` is the full canonical role-effective vector keyed `<role>.<stat>` (e.g.
 * `dps.atk`) WHEN AVAILABLE. A missing key is unknown, never zero. `meanEarned`
 * stays `null` for a censored/unresolved candidate even when `partialMean` exists.
 *
 * This module is presentation only. It invents no game fact, reward, stat, rule,
 * tolerance or classification: every value is a backend measurement or explicit
 * unknown rendered verbatim. Nothing here is a fallback protocol.
 */

/** Full measured stat vector for one point, keyed `<role>.<stat>` (e.g. `dps.spd`). */
export type PlayerRegionStatVector = Record<string, number>;

/** A placed unit inside the archetype's fixed formation (grid slot -> unit name). */
export type PlayerRegionPlacement = {
  grid: number | null;
  name: string | null;
};

/** The formation an archetype holds fixed. `placed` is null when placement is unresolved. */
export type PlayerRegionFormation = {
  roster: string[];
  placed: PlayerRegionPlacement[] | null;
  placementStatus: string | null;
};

/** One unit's fixed skills / invocation levels / weapon for the archetype. */
export type PlayerRegionSkillEntry = {
  unit: string | null;
  /** Skill ids verbatim; the bridge may publish these as numbers, normalised to strings for display. */
  skills: string[];
  /** Raw invocation levels from the schema; rendered generically, never re-derived. */
  invocationLevels: unknown;
  /** Weapon id verbatim (string or numeric on the wire); normalised to a string, `0` kept. */
  weaponId: string | null;
};

/**
 * The archetype's fixed requirements. This is an OBJECT on the wire (`formation`,
 * `skills`, `policy`, `other`) - not an array. `formation`/`skills` are null when the
 * scenario could not be resolved; `other` then carries the explicit unknown reason.
 */
export type PlayerRegionFixedRequirements = {
  formation: PlayerRegionFormation | null;
  skills: PlayerRegionSkillEntry[] | null;
  /** The finish policy string the group holds fixed (e.g. `after-ending`), or null. */
  policy: string | null;
  /** Canonical context: context, structuralKey, encounterId, defeatCount, tickLimit,
   *  startProfile, revisions, and (when unresolved) status/reason. Rendered generically. */
  other: Record<string, unknown> | null;
};

/** One measured point inside a region. Every field is a backend value or explicit unknown. */
export type PlayerRegionPoint = {
  candidateId: string | null;
  label: string | null;
  stats: PlayerRegionStatVector;
  /** Measured mean; stays null for a censored/unresolved candidate (never rewritten to 0). */
  meanEarned: number | null;
  /** Partial (win/loss-unresolved) mean; may exist while `meanEarned` is null. */
  partialMean: number | null;
  samples: number | null;
  total: number | null;
  unknownCount: number | null;
  /** Opaque uncertainty payload (e.g. {kind, pairedCount, diagnosticInterval}); rendered generically. */
  uncertainty: unknown;
  median?: number | null;
  quantiles?: Record<string, number> | null;
  /** Opaque threshold probabilities; rendered verbatim (never a hard-coded reference percentage). */
  thresholds?: unknown;
  /** Backend classification verbatim (e.g. `reference`, `measured`, `supported-degraded`). */
  classification: string | null;
  /** Evidence window verbatim (e.g. `development`, `confirmed-holdout`, `historical-unconfirmed`). */
  evidenceStatus: string | null;
  referenceId?: string | null;
  /** The reference candidate's mean when the backend reports its own comparison mean (verbatim). */
  referenceMeanEarned?: number | null;
  /** Reported retention tolerance for this point (verbatim; never a hard-coded reference percentage). */
  tolerance?: number | null;
  changedFields?: string[] | null;
  fixedFields?: unknown;
  context: string | null;
  reachability: string | null;
  resourcePolicy: unknown;
};

/** One region: a group of structurally compatible measured points and its explicit gaps. */
export type PlayerRegion = {
  id: string | null;
  label: string | null;
  /** `tested-alternatives` | `fixed-build` | `compensated` | `support` (kept as a string). */
  kind: string | null;
  conditions: string[];
  points: PlayerRegionPoint[];
  /** Explicit unbracketed gaps / failed tolerance tests - rendered, never interpolated. */
  gaps: string[];
  /** Controlled-comparison low-impact claims only; empty when unknown. */
  lowImpact: string[];
  /** Controlled boundary support strings only; empty when unknown. */
  support: string[];
  continuousCoverage: boolean | null;
  safeCartesianProduct: boolean | null;
};

/** One archetype: a structural identity and its regions. */
export type PlayerRegionArchetype = {
  id: string;
  label: string | null;
  description: string | null;
  fixedRequirements: PlayerRegionFixedRequirements | null;
  context: string | null;
  reachability: string | null;
  regions: PlayerRegion[];
  unknowns: string[];
};

/** The scope the summary was measured under. */
export type PlayerRegionScope = {
  encounterId: number | string | null;
  referenceId: string | null;
  mode: string | null;
};

/** Where a summary might be built from. `candidateLimit`/`availableCandidates` warn on truncation. */
export type PlayerRegionCoverage = {
  candidateLimit: number | null;
  availableCandidates: number | null;
  includedCandidates: number | null;
  truncated: boolean | null;
};

/** The root read model. */
export type PlayerRegionsSummary = {
  version: string | null;
  synthesisRevision: string | null;
  sourceRevision: string | null;
  /** `available` | `unavailable` (kept as a string). */
  status: string | null;
  /** Present when `status` is `unavailable`: why no read model exists. */
  reason: string | null;
  scope: PlayerRegionScope | null;
  archetypes: PlayerRegionArchetype[];
  coverage: PlayerRegionCoverage | null;
  limitations: string[];
};

/** A local read-only filter over the already-loaded summary. Never a search request. */
export type PlayerRegionQuery = {
  /** Any `<role>.<stat>` key actually present in the loaded points' `stats`. */
  field: string;
  /** Exact measured value; blank is `null` (no exact constraint). */
  exact: number | null;
  low: number | null;
  high: number | null;
  /** Optional player mean-earned target: highlights options, never discards them. */
  meanTarget: number | null;
};

/** One measured option a query resolved to, flattened for rendering. */
export type PlayerRegionQueryMatch = {
  archetypeId: string;
  archetypeLabel: string | null;
  archetypePolicy: string | null;
  archetypeContext: string | null;
  regionId: string | null;
  regionLabel: string | null;
  regionKind: string | null;
  /** `stats[field]` for this exact point (the measured knob value). */
  value: number;
  classification: string | null;
  evidenceStatus: string | null;
  meanEarned: number | null;
  partialMean: number | null;
  /** True when `meanEarned` is null but a partial mean exists (censored, not zero). */
  rewardUnresolved: boolean;
  samples: number | null;
  total: number | null;
  unknownCount: number | null;
  /** True when `unknownCount` is a positive count: the measurement is not complete. */
  hasUnknown: boolean;
  uncertainty: unknown;
  thresholds: unknown;
  median: number | null;
  candidateId: string | null;
  referenceId: string | null;
  /** The backend's own reference mean for this point, when published (verbatim; may be null). */
  referenceMeanEarned: number | null;
  /** The backend's reported retention tolerance for this point, when published (verbatim). */
  tolerance: number | null;
  /**
   * `referenceMeanEarned * tolerance` when BOTH are published (verbatim inputs, never a hard-coded
   * percentage); `null` when either is missing.
   */
  referenceRequirement: number | null;
  /**
   * `true` when the measured mean is at/above `referenceRequirement`, `false` when below, `null` when
   * the mean or the reference band is unknown - it is never assumed.
   */
  meetsReference: boolean | null;
  changedFields: string[];
  fixedFields: unknown;
  context: string | null;
  reachability: string | null;
  /** `true`/`false` against the optional mean target, or `null` when no target was set. */
  meetsMeanTarget: boolean | null;
  /** Point has no measured value on the queried field (sparse vector) - shown, not dropped. */
  vectorCompleteForField: boolean;
  /** Stat keys the point does not publish (unknown, not zero), relative to the summary union. */
  missingStats: string[];
  point: PlayerRegionPoint;
};

/** One sparse point surfaced by the query so a missing measurement is not silently dropped. */
export type PlayerRegionSparsePoint = {
  archetypeId: string;
  archetypeLabel: string | null;
  regionId: string | null;
  regionLabel: string | null;
  regionKind: string | null;
  candidateId: string | null;
  classification: string | null;
  evidenceStatus: string | null;
  meanEarned: number | null;
  missingStats: string[];
  point: PlayerRegionPoint;
};

/** What a local query resolved to, including the explicit-unknown and retained cases. */
export type PlayerRegionQueryResult = {
  mode: "exact" | "range" | "all" | "no-field";
  field: string;
  /** Measured points whose `stats[field]` satisfies the query (never pooled across regions). */
  matches: PlayerRegionQueryMatch[];
  /** Points that publish no measured value on `field` (unknown), retained for honesty. */
  sparse: PlayerRegionSparsePoint[];
  /** True when the query resolved to no measured point at all. */
  unknown: boolean;
  unknownReason: string | null;
  /** Known measured values for the queried field, sorted; never interpolated. */
  nearbyMeasured: number[];
  /** Matches kept even though they do not meet the optional mean target. */
  retainedBelowTarget: number;
  /** Matches with a positive `unknownCount`: eligibility is not fully measured. */
  matchesWithUnknown: number;
};

/**
 * The observed span of measured reward means (`meanEarned`) across every loaded point. Like the
 * stat-field span it is an OBSERVED extent only - NOT a safe/viable reward range - and is used only
 * to bound the reward-target slider. Returns null when no measured mean is published.
 */
export type PlayerRegionMeanSpan = {
  min: number;
  max: number;
  /** How many measured points publish a mean (>= 1 when a span exists). */
  count: number;
};

/**
 * One measured option in the reward-target view. This is independent of any stat field: it is a
 * whole candidate, grouped by the optional reward target the player is aiming for. Every value is a
 * backend measurement or an explicit unknown - no interpolation and no guarantee is produced here.
 */
export type PlayerRegionRewardOption = {
  archetypeId: string;
  archetypeLabel: string | null;
  archetypePolicy: string | null;
  archetypeContext: string | null;
  regionId: string | null;
  regionLabel: string | null;
  regionKind: string | null;
  candidateId: string | null;
  classification: string | null;
  evidenceStatus: string | null;
  /** Measured mean reward; null when censored/unresolved (never rewritten to 0). */
  meanEarned: number | null;
  partialMean: number | null;
  rewardUnresolved: boolean;
  samples: number | null;
  total: number | null;
  unknownCount: number | null;
  hasUnknown: boolean;
  uncertainty: unknown;
  thresholds: unknown;
  median: number | null;
  referenceId: string | null;
  referenceMeanEarned: number | null;
  tolerance: number | null;
  referenceRequirement: number | null;
  meetsReference: boolean | null;
  changedFields: string[];
  fixedFields: unknown;
  context: string | null;
  reachability: string | null;
  /** true/false against the reward target; null when no target is set OR the mean is unknown. */
  meetsMeanTarget: boolean | null;
  point: PlayerRegionPoint;
};

/**
 * The reward target only ORGANISES measured options. Options whose mean meets the target are listed
 * first; lower-reward options stay accessible in `below` (a separate collapsed view) and are never
 * removed; options whose mean is unknown are kept separately in `unknown` and are never treated as
 * meeting or as below. When no target is set every mean-known option is a `meets` entry.
 */
export type PlayerRegionRewardResult = {
  target: number | null;
  meets: PlayerRegionRewardOption[];
  below: PlayerRegionRewardOption[];
  unknown: PlayerRegionRewardOption[];
  /** Count of mean-known options below the target (0 when no target is set). */
  retainedBelowTarget: number;
  /** Count of options with an unknown/censored mean (not fully known). */
  unknownMean: number;
  /** Count of options with a positive resolved-observation gap (`unknownCount > 0`). */
  withUnknownObservations: number;
};

/** Props for the read-only measured-build explorer component. */
export type OptimizerPlayerRegionsProps = {
  /**
   * Loads the summary. The page wires this to `api.encounter_player_regions`. It is called from an
   * explicit button press, and also on mount / scope change when `autoLoad` is true - never on the
   * status poll and never per keystroke.
   */
  loadSummary: () => Promise<unknown>;
  /**
   * A stable key for the current scope/library/revision. When it changes the loaded summary is
   * invalidated (cleared), so a summary measured for one encounter is never shown for another.
   */
  scopeKey: string;
  /** Human label for the current scope, shown in the header. */
  scopeLabel?: string | null;
  /** Whether the host build exposes `encounter_player_regions`. */
  available?: boolean;
  /**
   * When true, load once on mount and once per `scopeKey` change (guarded by the same stale-response
   * check as a manual load). It never fires from the status poll and never re-fetches per keystroke.
   * Defaults to false: the explicit button press remains the only loader unless the host opts in.
   */
  autoLoad?: boolean;
  /** Existing page callback: open one exact tested build's drilldown. */
  onSelectCandidate?: ((candidateId: string) => void) | null;
  disabled?: boolean;
};
