/**
 * Player-team comparison over saved-loadout teams (client side of `ka-battle-eval-1` /
 * `ka-battle-search-1`).
 *
 * This module is transport + presentation only. It converts each candidate with the existing
 * `battleSetupToCombatScenario` adapter, hands the candidates to `/api/battle-run`, and reads the
 * contract in `eval-contract.md` back. It never re-ranks on its own and never claims a global
 * optimum: the ranking comes from the backend and is labelled with the backend's own metric.
 */
import {
  battleSetupToCombatScenario,
  type BattleSetupVisual,
  type CombatScenario,
} from "@/lib/battle-setup-adapter";
import {
  validateBattleSetup,
  type BattleSetup,
  type PetMonsterUnit,
  type PlayerUnit,
  type SetupIssue,
} from "@/lib/battle-setup";
import { parseBattleReplayResult, type BattleReplayResult } from "@/lib/battle-replay-result";
import { writeGeneratedBattle, type GeneratedBattleRecord } from "@/lib/generated-battle-store";

export const COMPARISON_EVAL_SCHEMA = "ka-battle-eval-1";
export const COMPARISON_SEARCH_SCHEMA = "ka-battle-search-1";
export const COMPARISON_ENDPOINT = "/api/battle-run";

/** Mirrors the backend hard caps; the request is rejected locally before any run. */
export const COMPARISON_CAPS = {
  maxRuns: 256,
  maxSeeds: 64,
  maxCandidates: 32,
  maxVariants: 32,
  maxTicks: 10000,
} as const;

export type ComparisonLevel = { encounterId: number; defeatCount: number; label?: string };

export type ComparisonCandidate = {
  id: string;
  label: string;
  setup: BattleSetup;
  scenario: CombatScenario;
  visualSetup: BattleSetupVisual;
  /** WARNING / UNKNOWN setup issues, kept with the candidate instead of being dropped */
  issues: SetupIssue[];
};

export type CandidateRejection = { code: string; message: string };

export type ComparisonSettings = {
  levels: ComparisonLevel[];
  sampleCount: number;
  baseSeed: number;
  tickLimit: number;
  maxRuns: number;
  /** Bounded generation of legal variants from each candidate's own validated units. */
  strategy?: ComparisonStrategy;
};

/**
 * `ka-battle-search-1` strategy request. Both toggles reorder the candidate's own validated
 * inputs only: the native placement tie for `ownUnits` order, and one native pass for skill order.
 */
export type ComparisonStrategy = {
  formations: boolean;
  skillPriorities: boolean;
  maxVariantsPerCandidate: number;
};

export const DEFAULT_COMPARISON_STRATEGY: ComparisonStrategy = {
  formations: true,
  skillPriorities: true,
  maxVariantsPerCandidate: 8,
};

export type GeneratedVariant = {
  id: string;
  kind: "base" | "formation" | "skillPriority" | "formation+skillPriority";
  label: string;
  description: string;
  changes: {
    ownUnitsOrder?: string[];
    skillOrder?: Array<{ unit: string; skills: number[]; invocationLevels: number[] }>;
  };
};

export type GeneratedCandidateReport = {
  available: number;
  generated: number;
  truncated: boolean;
  cap: number;
  formation: {
    enabled: boolean;
    arrangements: number;
    classes: Array<{ priority: number; effectiveDefense: number; members: string[]; unitsInTie: number; distinctOrderings: number }>;
    reason: string | null;
  };
  skillPriorities: {
    enabled: boolean;
    arrangements: number;
    units: Array<{ name: string; skills: number[]; invocationLevels: number[]; orderRelevantArrangements: number }>;
    reason: string | null;
  };
  variants: GeneratedVariant[];
};

export type GeneratedSummary = {
  availableVariants: number;
  generatedVariants: number;
  perCandidate: Record<string, GeneratedCandidateReport>;
  rule: string;
};

/** The `ka-battle-search-1` envelope: the evaluation result plus the bounded enumeration block. */
export type BattleSearchResult = BattleEvaluationResult & {
  claim: string;
  enumeration: {
    candidates: number;
    variants: number;
    combinations: number;
    levels: number;
    seeds: number;
    plannedRuns: number;
    variantIds: string[];
    variantPatchKeys: string[];
    rule: string;
    strategy?: ComparisonStrategy;
    generated?: GeneratedSummary;
  };
};

export type ComparisonStatistics = {
  samples: number;
  mean: number | null;
  variance: number | null;
  standardDeviation: number | null;
  standardError: number | null;
  descriptiveRange: [number, number] | null;
  interval95: null;
  interval95SuppressedReason: string;
  label: string;
};

export type ChestBlock = {
  histogram: Array<{ count: number; frequency: number }>;
  statistics: ComparisonStatistics;
  basis: string;
};

export type ComparisonLevelResult = {
  level: { encounterId: number; defeatCount: number; level: number | null; label: string | null };
  seeds: number[][];
  runs: number;
  resolvedRuns: number;
  wins: number;
  losses: number;
  censored: number;
  winProbability: number | null;
  lossProbability: number | null;
  censoredProbability: number | null;
  probabilities: { label: string; basis: string };
  queuedChestCallbacks: ChestBlock;
  dispatchedChests: ChestBlock;
  retainedInventory: { state: string; counted: boolean; note: string };
  censoring: { censored: number; note: string; stopReason: string | null };
  eventEvidence: {
    leavingEntries: number;
    prizeEventRecords: number;
    prizeTreasureIds: Array<number | null>;
    source: string;
  };
  errors: string[];
};

export type ComparisonCandidateResult = {
  id: string;
  label: string | null;
  status: "measured" | "partial" | "rejected";
  sweptKeysReplaced: string[];
  rank: number | null;
  rankMetric: string;
  pooled: {
    runs: number;
    resolvedRuns: number;
    wins: number;
    losses: number;
    censored: number;
    winProbability: number | null;
    lossProbability: number | null;
    censoredProbability: number | null;
    queuedChestCallbacks: ChestBlock;
    dispatchedChests: ChestBlock;
    retainedInventory: { state: string; counted: boolean; note: string };
    label: string;
  };
  levels: ComparisonLevelResult[];
  errors: string[];
};

export type RankingEntry = {
  id: string;
  rank: number;
  expectedDispatchedChestsPerAttempt: number | null;
  winProbability: number | null;
};

export type UnrankedEntry = { id: string; status: string; reasons: string[]; errors: string[] };

export type BattleEvaluationResult = {
  schema: string;
  status: string;
  request: { levels: ComparisonLevel[]; seeds: number[][]; tickLimit: number; limits: Record<string, number> };
  budget: { plannedRuns: number; candidates: number; levels: number; seeds: number; maxRuns: number };
  comparability: { scope: unknown; groups: Record<string, string[]>; rule: string };
  candidates: ComparisonCandidateResult[];
  rankings: {
    metric: string;
    metricNote: string;
    perLevel: Array<{ level: ComparisonLevelResult["level"]; ranked: RankingEntry[]; unranked: UnrankedEntry[] }>;
    pooled: { ranked: RankingEntry[]; unranked: UnrankedEntry[] };
  };
  ranking: string[];
  unranked: UnrankedEntry[];
  selected: {
    candidateId: string;
    level: ComparisonLevelResult["level"];
    seed: number[];
    rank: number;
    rankMetric: string;
    replaySelection: string;
    /** Present on `ka-battle-search-1`: the generated variant and combination that won. */
    variantId?: string;
    combinationId?: string;
  } | null;
  replay: unknown;
  strategies: Array<{
    id: string;
    title: string;
    status: string;
    mechanism: string;
    evidenceRule: string;
    sources: string[];
    confirmableNow: string[];
    incompleteInputs: string[];
    notes: string[];
  }>;
  caveats: string[];
  support: { mode: string; claim: string; exactReplaySupported: boolean; recommendationsSupported: boolean };
  caps: Record<string, number>;
};

export class BattleComparisonError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "BattleComparisonError";
    this.code = code;
  }
}

/** Modest default budget: 1 level, 2 samples, 4 000 ticks, 16 runs. */
export function defaultComparisonSettings(encounterId: number, defeatCount = 0): ComparisonSettings {
  return {
    levels: [{ encounterId, defeatCount }],
    sampleCount: 2,
    baseSeed: 1000,
    tickLimit: 4000,
    maxRuns: 16,
    strategy: { ...DEFAULT_COMPARISON_STRATEGY },
  };
}

/**
 * Build a candidate from a validated setup. ERROR-level issues (including a skill the native
 * runner cannot execute) reject the candidate here instead of being hidden or silently stripped.
 */
export function createComparisonCandidate(
  id: string,
  label: string,
  setup: BattleSetup,
): { ok: true; candidate: ComparisonCandidate } | { ok: false; errors: CandidateRejection[] } {
  const issues = validateBattleSetup(setup);
  const errors = issues.filter((issue) => issue.category === "ERROR");
  if (errors.length > 0) {
    return {
      ok: false,
      errors: errors.map((issue) => ({ code: issue.code, message: issue.message })),
    };
  }
  try {
    const { scenario, visualSetup } = battleSetupToCombatScenario(setup);
    return {
      ok: true,
      candidate: {
        id,
        label,
        setup,
        scenario,
        visualSetup,
        issues: issues.filter((issue) => issue.category !== "ERROR"),
      },
    };
  } catch (error) {
    return {
      ok: false,
      errors: [
        {
          code: "ADAPTER_REJECTED",
          message: error instanceof Error ? error.message : String(error),
        },
      ],
    };
  }
}

/** Deterministic, distinct [mathSeed, libSeed] pairs for the requested sample count. */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .filter(([, entry]) => entry !== undefined)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, entry]) => `${JSON.stringify(key)}:${canonicalJson(entry)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

/**
 * The declared input/source policy a candidate is evaluated under. Two teams are only comparable
 * when this is identical: a different item row, stock, herb budget, scheduled input, start profile
 * or finish policy is a different experiment (and extra-item strategies must not be rewarded
 * invisibly by being pooled with a cheaper policy).
 */
export function comparisonPolicyKey(scenario: CombatScenario): string {
  return canonicalJson({
    holyHerbStock: scenario.holyHerbStock,
    items: scenario.items ?? null,
    itemStock: scenario.itemStock ?? null,
    inputs: scenario.inputs ?? [],
    startProfile: scenario.startProfile ?? null,
    finishPolicy: scenario.finishPolicy ?? null,
    prePlacement: scenario.prePlacement ? Object.keys(scenario.prePlacement).sort() : null,
  });
}

/** Human-readable identity of the declared policy, for the comparison UI and its error text. */
export function comparisonPolicySummary(candidate: ComparisonCandidate): {
  key: string;
  holyHerbStock: number;
  itemStock: Record<string, number>;
  itemNames: string[];
  inputs: number;
  finishPolicy: string | null;
  startProfileKind: string | null;
} {
  const scenario = candidate.scenario;
  return {
    key: comparisonPolicyKey(scenario),
    holyHerbStock: scenario.holyHerbStock,
    itemStock: scenario.itemStock ?? {},
    itemNames: Object.keys(scenario.items ?? {}),
    inputs: (scenario.inputs ?? []).length,
    finishPolicy: scenario.finishPolicy ?? null,
    startProfileKind: scenario.startProfile?.kind ?? null,
  };
}

/** Candidates whose declared policy differs from the first candidate, with the exact key mismatch. */
export function comparisonPolicyProblems(candidates: ComparisonCandidate[]): string[] {
  if (candidates.length < 2) return [];
  const first = comparisonPolicySummary(candidates[0]);
  const problems: string[] = [];
  for (const candidate of candidates.slice(1)) {
    const summary = comparisonPolicySummary(candidate);
    if (summary.key === first.key) continue;
    const differences: string[] = [];
    if (summary.holyHerbStock !== first.holyHerbStock) {
      differences.push(`holyHerbStock ${summary.holyHerbStock} vs ${first.holyHerbStock}`);
    }
    if (canonicalJson(summary.itemStock) !== canonicalJson(first.itemStock)) {
      differences.push(`itemStock ${JSON.stringify(summary.itemStock)} vs ${JSON.stringify(first.itemStock)}`);
    }
    if (canonicalJson(summary.itemNames) !== canonicalJson(first.itemNames)) {
      differences.push(`items ${JSON.stringify(summary.itemNames)} vs ${JSON.stringify(first.itemNames)}`);
    }
    if (summary.inputs !== first.inputs) differences.push(`${summary.inputs} input(s) vs ${first.inputs}`);
    if (summary.finishPolicy !== first.finishPolicy) {
      differences.push(`finishPolicy ${summary.finishPolicy} vs ${first.finishPolicy}`);
    }
    if (summary.startProfileKind !== first.startProfileKind) {
      differences.push(`startProfile ${summary.startProfileKind} vs ${first.startProfileKind}`);
    }
    problems.push(
      `${candidate.id} declares a different input/source policy than ${candidates[0].id} (${differences.join("; ") || "comparison key mismatch"}). Group incompatible policies separately instead of pooling them.`,
    );
  }
  return problems;
}

export function deterministicSeedPairs(baseSeed: number, count: number): Array<[number, number]> {
  const seeds: Array<[number, number]> = [];
  const seen = new Set<string>();
  let math = (Math.abs(Math.trunc(baseSeed)) % 2147483647) + 1;
  let lib = math;
  while (seeds.length < count) {
    math = (math * 48271) % 2147483647;
    lib = (lib * 16807 + 11) % 2147483647;
    const key = `${math}:${lib}`;
    if (seen.has(key)) continue;
    seen.add(key);
    seeds.push([math, lib]);
  }
  return seeds;
}

export function comparisonBudget(settings: ComparisonSettings, candidateCount: number) {
  const seeds = deterministicSeedPairs(settings.baseSeed, settings.sampleCount);
  const runs = candidateCount * settings.levels.length * seeds.length;
  const problems: string[] = [];
  if (settings.sampleCount < 1) problems.push("sampleCount must be at least 1");
  if (settings.levels.length === 0) problems.push("at least one encounter level is required");
  if (candidateCount === 0) problems.push("at least one candidate team is required");
  if (candidateCount > COMPARISON_CAPS.maxCandidates) problems.push(`candidates exceed ${COMPARISON_CAPS.maxCandidates}`);
  if (seeds.length > COMPARISON_CAPS.maxSeeds) problems.push(`seeds exceed ${COMPARISON_CAPS.maxSeeds}`);
  if (settings.tickLimit > COMPARISON_CAPS.maxTicks) problems.push(`tickLimit exceeds ${COMPARISON_CAPS.maxTicks}`);
  if (runs > settings.maxRuns) problems.push(`planned ${runs} runs exceed the requested budget ${settings.maxRuns}`);
  if (runs > COMPARISON_CAPS.maxRuns) problems.push(`planned ${runs} runs exceed the hard cap ${COMPARISON_CAPS.maxRuns}`);
  return { seeds, runs, problems };
}

export function buildEvaluationRequest(
  settings: ComparisonSettings,
  candidates: ComparisonCandidate[],
): { ok: true; request: Record<string, unknown> } | { ok: false; problems: string[] } {
  const budget = comparisonBudget(settings, candidates.length);
  const problems = [...budget.problems, ...comparisonPolicyProblems(candidates)];
  if (problems.length > 0) return { ok: false, problems };
  return {
    ok: true,
    request: {
      schema: COMPARISON_EVAL_SCHEMA,
      levels: settings.levels.map((level) => ({
        encounterId: level.encounterId,
        defeatCount: level.defeatCount,
        ...(level.label ? { label: level.label } : {}),
      })),
      seeds: budget.seeds,
      tickLimit: settings.tickLimit,
      limits: {
        maxRuns: settings.maxRuns,
        maxTicks: COMPARISON_CAPS.maxTicks,
        maxSeeds: COMPARISON_CAPS.maxSeeds,
        maxCandidates: COMPARISON_CAPS.maxCandidates,
        maxVariants: COMPARISON_CAPS.maxVariants,
      },
      candidates: candidates.map((candidate) => ({
        id: candidate.id,
        label: candidate.label,
        scenario: candidate.scenario,
      })),
    },
  };
}

/** POST one comparison envelope. Preserves the backend error code and message verbatim. */
export async function runComparison(
  request: Record<string, unknown>,
  fetchImpl: typeof fetch = fetch,
): Promise<BattleEvaluationResult> {
  const response = await fetchImpl(COMPARISON_ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  const body = await response.text();
  if (!response.ok) {
    let code = `http-${response.status}`;
    let message = body;
    try {
      const parsed = JSON.parse(body) as { code?: string; message?: string };
      if (parsed.code) code = parsed.code;
      if (parsed.message) message = parsed.message;
    } catch {
      /* keep the raw body */
    }
    throw new BattleComparisonError(code, message);
  }
  const parsed = JSON.parse(body) as BattleEvaluationResult;
  if (parsed.schema !== COMPARISON_EVAL_SCHEMA) {
    throw new BattleComparisonError("unexpected-schema", `expected ${COMPARISON_EVAL_SCHEMA}, got ${parsed.schema}`);
  }
  return parsed;
}

export function candidateResult(
  result: BattleEvaluationResult,
  candidateId: string,
): ComparisonCandidateResult | null {
  return result.candidates.find((candidate) => candidate.id === candidateId) ?? null;
}

export function formatProbability(value: number | null): string {
  return value === null ? "n/a" : `${(value * 100).toFixed(1)}%`;
}

export function formatMean(value: number | null): string {
  return value === null ? "n/a" : value.toFixed(3);
}

export function formatHistogram(histogram: ChestBlock["histogram"]): string {
  if (histogram.length === 0) return "no resolved runs";
  return histogram.map((bucket) => `${bucket.count}x:${bucket.frequency}`).join(", ");
}

/** The winner's own adapter visualSetup plus the backend replay for exactly that winner. */
export function winnerTransfer(result: BattleEvaluationResult, candidates: ComparisonCandidate[]) {
  if (!result.selected) return null;
  const candidate = candidates.find((entry) => entry.id === result.selected?.candidateId);
  if (!candidate) return null;
  return {
    candidate,
    selected: result.selected,
    replay: result.replay,
    warnings: candidate.issues.map((issue) => `${issue.category}:${issue.code}`),
  };
}

/**
 * Store the backend's winner replay with the ACTUAL winning candidate's visualSetup and open path.
 * The backend replay already carries finish/postFinish; nothing here rebuilds a trace.
 */
export function storeWinnerReplay(
  result: BattleEvaluationResult,
  candidates: ComparisonCandidate[],
  encounterTitle: string | null,
): { ok: true; record: GeneratedBattleRecord } | { ok: false; reason: string } {
  const transfer = winnerTransfer(result, candidates);
  if (!transfer) return { ok: false, reason: "the comparison has no ranked winner" };
  const parsed = parseBattleReplayResult(transfer.replay);
  if (!parsed.result) return { ok: false, reason: `winner replay rejected: ${parsed.issues.join("; ")}` };
  const record = writeGeneratedBattle(
    parsed.result as BattleReplayResult,
    transfer.candidate.visualSetup,
    transfer.warnings,
    encounterTitle,
  );
  return { ok: true, record };
}

/** Honest one-line summary of the comparison claim, taken from the backend envelope. */
export function comparisonClaim(result: BattleEvaluationResult): string {
  return result.support?.claim ?? "bounded comparison of the supplied candidates";
}

export type ComparisonItemBudget = {
  policyKey: string | null;
  declared: {
    holyHerbStock: number;
    itemStock: Record<string, number>;
    itemNames: string[];
    inputs: number;
    finishPolicy: string | null;
    startProfileKind: string | null;
  } | null;
  /** Consumption read from the winner replay: engine item uses plus the stock left over. */
  winner: {
    candidateId: string;
    holyHerbRemaining: number | null;
    items: Array<{ name: string; remaining: number | null; uses: number }>;
    reasons: string[];
  } | null;
};

/**
 * The comparison's declared item budget and the winner's actual consumption. The declared policy is
 * identical for every candidate (enforced before the run); the spent/remaining numbers come from the
 * winner replay's engine item uses, so an item-heavy strategy is visible instead of invisibly pooling
 * with a cheaper one. `reasons` states why consumption is unavailable rather than reporting zero.
 */
export function comparisonItemBudget(
  result: BattleEvaluationResult,
  candidates: ComparisonCandidate[],
): ComparisonItemBudget {
  const first = candidates[0] ? comparisonPolicySummary(candidates[0]) : null;
  const budget: ComparisonItemBudget = {
    policyKey: first?.key ?? null,
    declared: first
      ? {
          holyHerbStock: first.holyHerbStock,
          itemStock: first.itemStock,
          itemNames: first.itemNames,
          inputs: first.inputs,
          finishPolicy: first.finishPolicy,
          startProfileKind: first.startProfileKind,
        }
      : null,
    winner: null,
  };
  if (!result.selected) return budget;
  const parsed = parseBattleReplayResult(result.replay);
  if (!parsed.result) {
    budget.winner = {
      candidateId: result.selected.candidateId,
      holyHerbRemaining: null,
      items: [],
      reasons: [`winner replay unavailable: ${parsed.issues.join("; ")}`],
    };
    return budget;
  }
  const replay = parsed.result;
  const declaredStock = replay.setupSummary?.itemStock ?? first?.itemStock ?? {};
  const uses = replay.itemUses ?? [];
  const reasons: string[] = [];
  if (uses.length === 0 && Object.keys(declaredStock).length > 0) {
    reasons.push("the winner replay records no item use; declared stock was not spent");
  }
  budget.winner = {
    candidateId: result.selected.candidateId,
    holyHerbRemaining: typeof replay.holyHerbRemaining === "number" ? replay.holyHerbRemaining : null,
    items: Object.keys(declaredStock).map((name) => {
      const used = uses.filter((entry) => entry.item === name).length;
      const remaining = replay.itemRemaining?.[name];
      return {
        name,
        remaining: typeof remaining === "number" ? remaining : null,
        uses: used,
      };
    }),
    reasons,
  };
  return budget;
}

export function unsupportedSkillNotice(issues: SetupIssue[]): string | null {
  const skillIssues = issues.filter(
    (issue) => issue.category === "ERROR" && /SKILL/i.test(issue.code),
  );
  if (skillIssues.length === 0) return null;
  return skillIssues.map((issue) => `${issue.code}: ${issue.message}`).join("; ");
}

/** Local floor for a strategy request; the backend validates the actual generated run plan. */
export function searchBudget(settings: ComparisonSettings, candidateCount: number) {
  const budget = comparisonBudget(settings, candidateCount);
  const strategy = settings.strategy ?? DEFAULT_COMPARISON_STRATEGY;
  const problems = [...budget.problems];
  if (strategy.maxVariantsPerCandidate < 0) problems.push("maxVariantsPerCandidate must not be negative");
  return { ...budget, strategy, problems, note: "generated variants add runs; the backend checks the real plan" };
}

/**
 * Build the `ka-battle-search-1` request with bounded variant generation. The evaluation budget
 * rules still apply to the disclosed levels/seeds/limits; the generated variants are capped by
 * `maxVariantsPerCandidate` and the response states whether the cap truncated the legal space.
 */
export function buildSearchRequest(
  settings: ComparisonSettings,
  candidates: ComparisonCandidate[],
): { ok: true; request: Record<string, unknown> } | { ok: false; problems: string[] } {
  const built = buildEvaluationRequest(settings, candidates);
  if (!built.ok) return built;
  const strategy = settings.strategy ?? DEFAULT_COMPARISON_STRATEGY;
  return {
    ok: true,
    request: {
      ...built.request,
      schema: COMPARISON_SEARCH_SCHEMA,
      variants: [],
      strategy: {
        formations: strategy.formations,
        skillPriorities: strategy.skillPriorities,
        maxVariantsPerCandidate: strategy.maxVariantsPerCandidate,
      },
    },
  };
}

/** POST one search envelope. Preserves the backend error code and message verbatim. */
export async function runSearchEnvelope(
  request: Record<string, unknown>,
  fetchImpl: typeof fetch = fetch,
): Promise<BattleSearchResult> {
  const response = await fetchImpl(COMPARISON_ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  const body = await response.text();
  if (!response.ok) {
    let code = `http-${response.status}`;
    let message = body;
    try {
      const parsed = JSON.parse(body) as { code?: string; message?: string };
      if (parsed.code) code = parsed.code;
      if (parsed.message) message = parsed.message;
    } catch {
      /* keep the raw body */
    }
    throw new BattleComparisonError(code, message);
  }
  const parsed = JSON.parse(body) as BattleSearchResult;
  if (parsed.schema !== COMPARISON_SEARCH_SCHEMA) {
    throw new BattleComparisonError("unexpected-schema", `expected ${COMPARISON_SEARCH_SCHEMA}, got ${parsed.schema}`);
  }
  return parsed;
}

function invocationSignature(skills: Array<{ skillId: number; invocationLevel: number }>): string {
  return skills.map((skill) => `${skill.skillId}:${skill.invocationLevel}`).sort().join(",");
}

/**
 * Apply one generated variant to the base candidate's own setup.
 *
 * The variant is only accepted when it is a permutation of THIS candidate's units and THIS
 * candidate's own skills/invocation levels, so the stored replay always shows the actual winning
 * team. Anything else is refused with a reason instead of rendering an unrelated team.
 */
export function applyGeneratedVariant(
  setup: BattleSetup,
  variant: GeneratedVariant | undefined,
): { ok: true; setup: BattleSetup } | { ok: false; reason: string } {
  if (!variant || variant.kind === "base") return { ok: true, setup };
  const changes = variant.changes ?? {};
  let playerTeam = setup.playerTeam;
  if (changes.ownUnitsOrder) {
    const pets = playerTeam.filter(
      (unit): unit is PetMonsterUnit => unit.kind === "monster" && Boolean(unit.petOwnerName),
    );
    const nonPets = playerTeam.filter((unit) => !pets.includes(unit as PetMonsterUnit));
    const names = new Set(nonPets.map((unit) => unit.name));
    const order = changes.ownUnitsOrder;
    if (order.length !== names.size || new Set(order).size !== order.length || !order.every((name) => names.has(name))) {
      return { ok: false, reason: "the variant order is not a permutation of this candidate's own units" };
    }
    const byName = new Map(nonPets.map((unit) => [unit.name, unit]));
    const ordered: PlayerUnit[] = [];
    for (const name of order) {
      ordered.push(byName.get(name)!);
      for (const pet of pets) if (pet.petOwnerName === name) ordered.push(pet);
    }
    for (const pet of pets) if (!ordered.includes(pet)) ordered.push(pet);
    playerTeam = ordered;
  }
  const skillOrder = changes.skillOrder ?? [];
  const byName = new Map(playerTeam.map((unit) => [unit.name, unit]));
  for (const entry of skillOrder) {
    const unit = byName.get(entry.unit);
    if (!unit) return { ok: false, reason: `the variant reorders skills of an unknown unit: ${entry.unit}` };
    if (entry.skills.length !== unit.skills.length) {
      return { ok: false, reason: `the variant changes the skill count of ${entry.unit}` };
    }
    if (entry.invocationLevels.some((level) => level !== 0 && level !== 1 && level !== 2)) {
      return { ok: false, reason: `the variant has an out-of-range invocation level for ${entry.unit}` };
    }
    const reordered = entry.skills.map((skillId, index) => ({
      skillId,
      invocationLevel: entry.invocationLevels[index] as 0 | 1 | 2,
    }));
    if (invocationSignature(reordered) !== invocationSignature(unit.skills)) {
      return { ok: false, reason: `the variant for ${entry.unit} is not a permutation of its own skills` };
    }
  }
  const withSkills = playerTeam.map((unit) => {
    const entry = skillOrder.find((candidate) => candidate.unit === unit.name);
    if (!entry) return unit;
    return {
      ...unit,
      skills: entry.skills.map((skillId, index) => ({
        skillId,
        invocationLevel: entry.invocationLevels[index] as 0 | 1 | 2,
      })),
    };
  });
  return { ok: true, setup: { ...setup, playerTeam: withSkills } };
}

/** The winning candidate + its winning generated variant, resolved to that variant's own visual. */
export function searchWinnerTransfer(result: BattleSearchResult, candidates: ComparisonCandidate[]) {
  if (!result.selected) return { ok: false as const, reason: "the search has no ranked winner" };
  const candidate = candidates.find((entry) => entry.id === result.selected?.candidateId);
  if (!candidate) {
    return { ok: false as const, reason: `no supplied candidate matches the winning id ${result.selected.candidateId}` };
  }
  const variantId = result.selected.variantId ?? "base";
  const report = result.enumeration?.generated?.perCandidate?.[candidate.id];
  const variant = report?.variants.find((entry) => entry.id === variantId);
  if (report && !variant) {
    return { ok: false as const, reason: `the winning variant ${variantId} is missing from the response` };
  }
  const applied = applyGeneratedVariant(candidate.setup, variant);
  if (!applied.ok) return { ok: false as const, reason: applied.reason };
  let visualSetup: BattleSetupVisual;
  try {
    ({ visualSetup } = battleSetupToCombatScenario(applied.setup));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false as const, reason: `the winning variant is not a legal setup: ${message}` };
  }
  return {
    ok: true as const,
    candidate,
    variant: variant ?? null,
    variantId,
    setup: applied.setup,
    visualSetup,
    replay: result.replay,
    warnings: candidate.issues.map((issue) => `${issue.category}:${issue.code}`),
  };
}

/** Store the search winner's replay with the ACTUAL winning variant's appearance. */
export function storeSearchWinnerReplay(
  result: BattleSearchResult,
  candidates: ComparisonCandidate[],
  encounterTitle: string | null,
): { ok: true; record: GeneratedBattleRecord } | { ok: false; reason: string } {
  const transfer = searchWinnerTransfer(result, candidates);
  if (!transfer.ok) return { ok: false, reason: transfer.reason };
  const parsed = parseBattleReplayResult(transfer.replay);
  if (!parsed.result) return { ok: false, reason: `winner replay rejected: ${parsed.issues.join("; ")}` };
  const record = writeGeneratedBattle(
    parsed.result as BattleReplayResult,
    transfer.visualSetup,
    transfer.warnings,
    encounterTitle,
  );
  return { ok: true, record };
}
