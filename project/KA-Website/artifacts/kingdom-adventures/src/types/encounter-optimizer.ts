/**
 * Encounter-aware optimiser bridge types.
 *
 * These describe the payload the Python host publishes as `snapshot.encounterAware` (the read
 * `Bridge.encounter_report()` returns) and the `encounter_preview` / `encounter_activate` /
 * `encounter_question` / `encounter_budget` / `encounter_rollback` commands. They are aligned to the
 * ACTUAL `strategy_encounter_search.Coordinator.report()` shape.
 *
 * The module stays deliberately loose: every field is optional, because the page must render a host
 * build that predates a field, and a field named here may be filled in later without a page change.
 * Missing numbers are never rendered as 0 - the component shows "unavailable" instead - so `null` and
 * `undefined` are both meaningful and always optional.
 *
 * This is presentation vocabulary, not game data: nothing here invents a stat, a reward or a rule.
 */
import type { EncounterHumanLabels } from "@/lib/encounter-human-labels";


/** A host command call, matching the page's `sendCommand(label, action, value)` signature. */
export type OptimizerCommandSender = (
  label: string,
  action: string,
  value: Record<string, unknown>,
) => void | Promise<void>;

/** The search streams still registered; `average` is retired and `freewill` is inactive. */
export type EncounterStreamKey =
  | "community"
  | "discovery"
  | "rebel"
  | "stumble"
  | "average"
  | "mechanism";

/** Finite purpose budgets, in whole runs, the host spreads across the encounter-aware work. */
export type EncounterPurposeKey =
  | "improvement"
  | "boundary"
  | "support"
  | "comparison"
  | "exploration";

export type EncounterMode = "community-first" | "all-strategy";

export type EncounterQuestionKind = "fixed-build" | "compensated" | "support";

/** Stream allocations are FRACTIONS of the pool (validated as finite, non-negative numbers). */
export type EncounterAllocations = Partial<Record<EncounterStreamKey, number>>;
/** Purpose budgets are whole RUN counts (validated as non-negative integers). */
export type EncounterPurposeBudgets = Partial<Record<EncounterPurposeKey, number>>;

/** Config v1. `allocations` are fractions; `purposes` are integer run budgets. */
export type EncounterAwareConfig = {
  version?: number | null;
  mode?: EncounterMode | string | null;
  allocations?: EncounterAllocations | null;
  purposes?: EncounterPurposeBudgets | null;
  /** Optional per-owner finite budget matrix, keyed by the backend's own owner names. */
  budgets?: Record<string, unknown> | null;
  /** Per-owner/per-purpose whole-run budget matrix, echoed by a host that publishes one. */
  budgetMatrix?: unknown;
  /**
   * User-selected reward thresholds (`P(earned >= K)`). An EXPLICIT list: the backend reports
   * exactly the K values it was handed and never infers a universal threshold set.
   */
  thresholds?: number[] | null;
  /** The encounter the scope targets, when the config carries it. */
  encounter?: number | null;
  /** The frozen reference id, when the config carries it. */
  referenceId?: string | null;
  /** The build-domain constraint document the activation was scoped with. */
  constraints?: unknown;
};

/** The frozen policy copied from the actual observed scenario, never a hardcoded default. */
export type EncounterQuestionPolicy = {
  finishPolicy?: string | null;
  tickLimit?: number | null;
  holyHerbStock?: number | null;
  startProfile?: string | null;
  inputs?: unknown;
  mpWatchUnits?: unknown;
  note?: string | null;
};

/** The finite reliability plan attached to a question, when the backend supplied one. */
export type EncounterQuestionReliability = {
  threshold?: number | null;
  minimumProbability?: number | null;
  [key: string]: unknown;
};

/**
 * The frozen question object `regions.question()` returns, plus the tested `value` the coordinator
 * keeps beside it. `field` is the canonical `ownUnits.<index>.parameters.<id>` path.
 */
export type EncounterAwareQuestion = {
  version?: string | null;
  id?: string | null;
  kind?: EncounterQuestionKind | string | null;
  referenceId?: string | null;
  referenceRevision?: number | null;
  encounterRevision?: string | null;
  mechanicsRevision?: string | null;
  policy?: EncounterQuestionPolicy | null;
  constraints?: unknown;
  /** Canonical `ownUnits.<index>.parameters.<id>` path. */
  field?: string | null;
  fixedFields?: Record<string, number> | null;
  adjustableFields?: string[] | null;
  /** Product choice; default 0.9. Never a measured range. */
  tolerance?: number | null;
  reliability?: EncounterQuestionReliability | null;
  /** The tested point the coordinator keeps beside the frozen question. */
  value?: number | null;
};

/** The coordinator's `questionBudget` slot: the frozen question plus its finite spend. */
export type EncounterQuestionSlot = {
  question?: EncounterAwareQuestion | null;
  budget?: number | null;
  spent?: number | null;
  minimumPairs?: number | null;
  referenceId?: string | null;
};

/** One boundary record the coordinator persisted after a tested point. */
export type EncounterTestedPoint = {
  candidateId?: string | null;
  value?: {
    field?: string | null;
    kind?: EncounterQuestionKind | string | null;
    value?: number | null;
  } | null;
  classification?: string | null;
  pairedCount?: number | null;
  diagnosticInterval?: unknown;
  uncertaintyMethod?: string | null;
};

export type EncounterUnknownGap = {
  candidateId?: string | null;
  value?: {
    field?: string | null;
    kind?: EncounterQuestionKind | string | null;
    value?: number | null;
  } | null;
  classification?: string | null;
  pairedCount?: number | null;
  reason?: string | null;
};

/** One persisted boundary row (a tested conditional point and its own gaps). */
export type EncounterBoundaryRow = {
  kind?: EncounterQuestionKind | string | null;
  experimentId?: number | string | null;
  frozenReference?: string | null;
  questionId?: string | null;
  testedPoints?: EncounterTestedPoint[] | null;
  unresolvedGaps?: EncounterUnknownGap[] | null;
  assessment?: unknown;
  published?: boolean | null;
  note?: string | null;
};

/** The report's diagnostic view over the boundary rows: arrays of the rows' points/gaps. */
export type EncounterMechanicalRegions = {
  points?: unknown;
  gaps?: unknown;
  note?: string | null;
};

/** One earned-reward portfolio row, aligned to the coordinator's own portfolio entry. */
export type EncounterPortfolioRow = {
  candidateId?: string | null;
  key?: string | null;
  label?: string | null;
  /** Mean over runs that carried a number (the partial mean). Never a confirmed full mean. */
  partialMean?: number | null;
  arithmeticMeanEarned?: number | null;
  /** Full mean; the coordinator leaves this null while any run is unresolved. */
  fullMean?: number | null;
  meanEarned?: number | null;
  resolved?: number | null;
  samples?: number | null;
  total?: number | null;
  unknownCount?: number | null;
  /** Mean / standard-error uncertainty block. */
  standardError?: number | null;
  uncertainty?: { kind?: string | null; value?: number | null } | number | null;
  /** Resolved fraction (resolved / total). NOT a win probability. */
  reliability?: number | null;
  lossFrequency?: number | null;
  /** Fraction of NUMERIC samples that earned exactly zero, over its own denominator. */
  zeroFrequency?: number | null;
  median?: number | null;
  min?: number | null;
  max?: number | null;
  stdev?: number | null;
  quantiles?: Record<string, number> | null;
  /** Mean resource uses per run and the fraction of runs that used any, when published. */
  resourceMean?: number | null;
  resourceFrequency?: number | null;
  resourceCount?: number | null;
  /**
   * `P(earned >= K)` per EXPLICIT threshold K. A bare number is the rate; the object form keeps the
   * hit count and the denominator so the rate is never shown without what it was measured over.
   */
  thresholds?:
    | Record<string, number | { count?: number | null; n?: number | null; frequency?: number | null }>
    | null;
  selectedThreshold?: number | null;
  features?: Record<string, unknown> | null;
  /** How many readings each published metric covers (total / numeric / resources). */
  metricSampleCounts?: { total?: number | null; numeric?: number | null; resources?: number | null } | null;
  /** The raw attempted/resolved/lost/unresolved denominators behind the summary. */
  denominators?: {
    attempted?: number | null;
    resolved?: number | null;
    lost?: number | null;
    unresolved?: number | null;
    errors?: number | null;
    censored?: number | null;
  } | null;
  compatibility?: unknown;
  window?: string | null;
  arm?: string | null;
  eligible?: boolean | null;
  /** Development vs confirmation WINDOW markers (booleans), not run counts. */
  development?: boolean | null;
  confirmation?: boolean | null;
  synthetic?: boolean | null;
  playerUnknown?: boolean | null;
  /** Build-domain labels the host attached to this candidate (never inferred by the page). */
  context?: string | null;
  provenanceStatus?: string | null;
  reachability?: string | null;
  pairedComparisons?: unknown[] | null;
  /** Legacy/exact build attribution, when a host build supplies it. */
  build?: string | null;
  parent?: string | null;
  parentChange?: string | null;
  /** Optional backend diagnostic/confirmation status, preserved as-is. */
  confirmed?: boolean | null;
  degraded?: boolean | null;
  unresolved?: boolean | null;
  status?: string | null;
  diagnostic?: string | null;
};

export type EncounterPurposeSpend = {
  /** The report's `total` field. `authorized` is an accepted alias. */
  total?: number | null;
  authorized?: number | null;
  reserved?: number | null;
  completed?: number | null;
};

/** One prior -> new allocation ledger line inside the migration preview. */
export type EncounterMigrationRow = {
  stream?: string | null;
  oldValue?: number | null;
  target?: string | null;
  newValue?: number | null;
  reason?: string | null;
  /** Legacy alias names an older host build may use. */
  key?: string | null;
  label?: string | null;
  prior?: number | null;
  next?: number | null;
};

/** The deterministic simulator-migration eligibility the host attaches to a preview. */
export type EncounterSimulatorMigration = {
  eligible?: boolean | null;
  required?: boolean | null;
  planId?: string | null;
  reason?: string | null;
  limitation?: string | null;
  preserves?: string[] | null;
  evidence?: unknown;
  proof?: unknown;
};

export type EncounterMigrationPreview = {
  /** The exact planned config the host would activate. */
  config?: EncounterAwareConfig | null;
  ledger?: EncounterMigrationRow[] | null;
  total?: number | null;
  rollback?: unknown;
  applied?: boolean | null;
  budgetsPreserved?: boolean | null;
  note?: string | null;
  purposes?: EncounterPurposeBudgets | null;
  simulatorMigration?: EncounterSimulatorMigration | null;
  /**
   * Per-owner/per-purpose whole-run budget matrix the host computed for the planned config. The
   * page only reads it; it never derives one, and a missing cell stays missing rather than 0.
   */
  budgetMatrix?: unknown;
  previousTracks?: Record<string, number> | null;
  /** Legacy alias shapes an older host build may publish. */
  rows?: EncounterMigrationRow[] | null;
  priorTotal?: number | null;
  nextTotal?: number | null;
  conserved?: boolean | null;
  /** The pure presentation helper payload echoed on the preview, when the host publishes it. */
  presentation?: EncounterPresentation | null;
  /** The scope the preview was produced for, echoed so the page can confirm it before activating. */
  encounter?: number | null;
  referenceId?: string | null;
  constraints?: unknown;
  thresholds?: number[] | null;
};

/** The current experiment when one is in flight, as the report echoes it. */
export type EncounterCurrentWork = {
  experimentId?: number | string | null;
  candidateId?: string | null;
  referenceId?: string | null;
  purpose?: EncounterPurposeKey | string | null;
  reserved?: number | null;
  completed?: number | null;
  blocked?: boolean | null;
  blockedReason?: string | null;
  planJobs?: number | null;
  /** Provided mechanical justification for the frozen proposal (nullable on older builds). */
  expectedMechanicalDifferences?: Record<string, unknown> | null;
  approximatelyPreserved?: Record<string, unknown> | null;
  whySimulation?: string | null;
  [key: string]: unknown;
};

export type EncounterAwareProgress = {
  roundIndex?: number | null;
  experiments?: number | null;
  reserved?: number | null;
  completed?: number | null;
  current?: EncounterCurrentWork | null;
  paired?: unknown[] | null;
  confirmation?: Record<string, unknown> | null;
  publication?: Record<string, unknown> | null;
  queueLength?: number | null;
  freshLibrary?: boolean | null;
  idle?: boolean | null;
  unspendable?: unknown[] | null;
  blocked?: boolean | null;
};

/** An optional host-published capability map for the question axis controls. */
export type EncounterQuestionField = {
  role?: string | null;
  stat?: string | null;
  label?: string | null;
  kinds?: string[] | null;
  /** Canonical `ownUnits.<index>.parameters.<id>` path, when the host mapped it. */
  field?: string | null;
  /** The actual effective prepared value at that path; never a default. */
  currentValue?: number | null;
  /** Source unit index (0-based) the role resolved to. */
  unitIndex?: number | null;
  parameterId?: number | null;
  /** Resolution status, e.g. `resolved` / `unresolved`. */
  status?: string | null;
  /** False when the host's constraint document may not move this field. */
  adjustable?: boolean | null;
};

/** One selectable encounter, read from the parent page's recovered catalogue (never hardcoded). */
export type EncounterChoice = {
  id: number;
  /** Human label: "Encounter 19 - Take out the Wairo Tank!". */
  label: string;
  title?: string | null;
  boss?: string | null;
  level?: number | null;
  enemyCount?: number | null;
  defeatCount?: number | null;
  difficulty?: string | null;
};

/** One fidelity-labelled mechanical quantity from the presentation helper. */
export type EncounterPresentationQuantity = {
  key?: string | null;
  label?: string | null;
  value?: number | null;
  unit?: string | null;
  /** `exact` | `reduced` | `approx` | `unavailable`. Never invented. */
  fidelity?: string | null;
  scope?: string | null;
  reason?: string | null;
  denominator?: number | null;
};

/** The reference encounter identity, from the same compiled roster the coordinator uses. */
export type EncounterPresentationEncounter = {
  encounterId?: number | null;
  title?: string | null;
  defeatCount?: number | null;
  level?: number | null;
  enemyCount?: number | null;
  bossName?: string | null;
  bossOrder?: number | null;
  revision?: string | null;
  referenceId?: string | null;
  note?: string | null;
};

/** The exact `strategy_build_domain.constraint_schema` echo for one constraint document. */
export type EncounterConstraintSchema = {
  context?: string | null;
  provenanceStatus?: string | null;
  valid?: boolean | null;
  violations?: Array<{ field?: string | null; reason?: string | null }> | null;
  fixed?: Record<string, { index?: number; parameterId?: number; value?: number }> | null;
  bounds?: Record<string, { index?: number; parameterId?: number; low?: number; high?: number }> | null;
};

/** The build-domain status the constraint editor renders beside its inputs. */
export type EncounterPresentationBuildDomain = {
  context?: string | null;
  provenanceStatus?: string | null;
  reachability?: string | null;
  schemaValid?: boolean | null;
  valid?: boolean | null;
  violations?: Array<{ field?: string | null; reason?: string | null }> | null;
  fixed?: Record<string, number> | null;
  bounds?: Record<string, [number, number]> | null;
  adjustableFields?: string[] | null;
  syntheticBounds?: Record<string, [number, number]> | null;
  /** EXACT `strategy_build_domain.constraint_schema` output, echoed for the editor. */
  constraintSchema?: EncounterConstraintSchema | null;
  /** role name -> source unit index (or null when the role is absent). */
  roleIndices?: Record<string, number | null> | null;
  formationOrder?: number[] | null;
  rolesStatus?: string | null;
  note?: string | null;
};

/** Reduced-model mechanical summary: quantities with their own exact/reduced/approx labels. */
export type EncounterPresentationMechanics = {
  reducedModel?: boolean | null;
  encounterRevision?: string | null;
  mechanicsRevision?: string | null;
  compilerRevision?: string | null;
  quantities?: EncounterPresentationQuantity[] | null;
  /** Always `unknown` here: this reduced model predicts no whole-battle reward. */
  wholeBattleReward?: string | null;
  approximations?: string[] | null;
  note?: string | null;
};

/**
 * The pure presentation payload `strategy_encounter_presentation.describe()` returns. The host
 * publishes it as `snapshot.presentation` (or inside `migrationPreview.presentation`); every field
 * is optional because a host build may predate it, and nothing here is inferred by the page.
 */
export type EncounterPresentation = {
  version?: string | null;
  referenceId?: string | null;
  encounterDetails?: EncounterPresentationEncounter | null;
  questionFields?: EncounterQuestionField[] | null;
  buildDomain?: EncounterPresentationBuildDomain | null;
  mechanicsSummary?: EncounterPresentationMechanics | null;
};

export type EncounterAwareSnapshot = {
  version?: number | string | null;
  enabled?: boolean | null;
  mode?: EncounterMode | string | null;
  config?: EncounterAwareConfig | null;
  /** Startup-loaded source stamp; a page refresh does not load a new backend revision. */
  runtimeRevision?: string | null;
  sessionId?: string | null;
  question?: EncounterAwareQuestion | null;
  questionBudget?: EncounterQuestionSlot | null;
  encounter?: number | Record<string, unknown> | null;
  mechanicalRegions?: EncounterMechanicalRegions | EncounterMechanicalRegions[] | null;
  budgets?: Record<string, unknown> | null;
  /** Per-owner/per-purpose whole-run budget matrix from the host report, when published. */
  budgetMatrix?: unknown;
  purposeSpending?: Record<string, EncounterPurposeSpend> | null;
  portfolio?: EncounterPortfolioRow[] | Record<string, EncounterPortfolioRow> | null;
  boundaries?: EncounterBoundaryRow[] | null;
  progress?: EncounterAwareProgress | null;
  timings?: Record<string, unknown> | null;
  limitations?: unknown;
  migrationPreview?: EncounterMigrationPreview | null;
  /** Optional capability fields; absent on a host build that has not published them yet. */
  supportedModes?: string[] | null;
  questionFields?: EncounterQuestionField[] | null;
  /**
   * The pure presentation payload `strategy_encounter_presentation.describe()` returns, when the
   * host publishes it. Read-only: the page maps canonical paths from here and never invents one.
   */
  presentation?: EncounterPresentation | null;
};

/** Props for the operating-regions panel. */
export type EncounterOptimizerPanelProps = {
  state: EncounterAwareSnapshot | null | undefined;
  sendCommand: OptimizerCommandSender;
  /** True while another host command is in flight. */
  disabled?: boolean;
  /** True when the host is paused, which is the only state a migration may be applied in. */
  paused?: boolean;
  /**
   * The parent page's own recovered encounter catalogue, so the panel selects a real encounter id
   * instead of hardcoding names. Optional: an older host may publish none.
   */
  encounterOptions?: EncounterChoice[] | null;
  /**
   * Optional read-only label overrides (unit/field names, reference names, a policy summary) so a
   * host can phrase its own vocabulary for the operator view. Missing overrides fall back to the
   * pure snapshot-derived labels; nothing is guessed when both are unknown.
   */
  humanLabels?: EncounterHumanLabels | null;
};
