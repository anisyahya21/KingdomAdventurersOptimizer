/**
 * Local-only strategy search client (transport + contract).
 *
 * The browser never computes scores, defaults or rankings. It asks
 * `GET /api/strategy-search/defaults` for the engine's own `default_request()`, posts the user's
 * request to `POST /api/strategy-search/jobs`, polls `GET /api/strategy-search/jobs/{id}` and can
 * `DELETE` the job to cancel it. While `KA-Website/tools/recovery/strategy_search.py` is absent the
 * defaults call answers 503 with a precise reason and this module surfaces that reason verbatim -
 * it never substitutes placeholder numbers.
 *
 * The engine module decides objectives, metric names and whether a real box-generation metric is
 * available; the UI renders whatever the result contains and labels unmodelled parts as limitations.
 */

import { parseBattleReplayResult, type BattleReplayResult } from "@/lib/battle-replay-result";
import { writeGeneratedBattle } from "@/lib/generated-battle-store";
export { downloadJson } from "@/lib/download-json";

export const STRATEGY_SEARCH_ENDPOINT = "/api/strategy-search";
export const STRATEGY_SEARCH_SCHEMA = "ka-strategy-search-1";
export const STRATEGY_SEARCH_RESULT_SCHEMA = "ka-strategy-search-result-1";
export const STRATEGY_SEARCH_DEFAULTS_SCHEMA = "ka-strategy-search-defaults-1";
/**
 * The engine's documented `searchAxis` values. The engine validates this field itself and rejects
 * anything else with 422 `problems`, so this list only drives the selector labels.
 */
export const STRATEGY_SEARCH_AXES = ["both", "formation", "skill-order"] as const;

export type StrategySearchSeed = { mathSeed: number; libSeed: number };

export type StrategySearchRequest = {
  schema?: string;
  objective?: string;
  searchAxis?: string;
  candidateCount?: number;
  searchSeed?: number;
  seeds?: StrategySearchSeed[];
  tickLimit?: number;
  /** Omitted entirely to use the engine's own default (UREF) base scenario. */
  baseScenario?: unknown;
};

export type StrategySearchBounds = {
  candidateCount: number;
  seeds: number;
  tickLimit: number;
  objectives: string[];
};

export type StrategySearchDefaults = {
  available: true;
  request: StrategySearchRequest;
  resultSchema: string;
  bounds: StrategySearchBounds;
};

export type StrategySearchUnavailable = {
  available: false;
  code: string;
  message: string;
  expectedPath?: string;
  problems?: string[];
  activeJobId?: string;
};

export type StrategyCandidateSummary = {
  candidateId: string;
  kind: string | null;
  axis: string | null;
  provisional: boolean | null;
  scoreDefinition: string | null;
  /** Why the engine left this entry unranked, or null when it was ranked. */
  unrankedReason: string | null;
  scenarioSha256: string | null;
  scenario?: unknown;
  /** The engine scores lexicographically (an array); keep the exact shape, never coerce it. */
  score: unknown;
  /** Baseline score recomputed on this candidate's identical paired seed subset, or null. */
  pairedBaselineScore: unknown;
  comparable: boolean | null;
  /** True when this entry's own runs completed (a boolean, never a run count). */
  completed: boolean | null;
  /** True when this entry itself finished every requested seed pair. */
  fullSeedBankComplete: boolean | null;
  /** Engine verdict: this entry's score beats its paired baseline score. */
  beatsBaseline: boolean | null;
  metrics: Record<string, unknown>;
  /** One raw run evaluation per seed pair: the engine returns an array, never a count. */
  evaluations: unknown[];
  /** The seed pairs this entry was actually paired on. */
  seedsUsed: unknown[];
};

export type StrategySearchAxisReport = {
  axis: string;
  distinctArrangements: number | null;
  arrangements: number | null;
  degenerateReason: string | null;
  units: Array<{ unit: string; orderRelevantArrangements: number }>;
};

export type StrategySearchSpace = {
  searchAxis: string | null;
  axis: string | null;
  axes: StrategySearchAxisReport[];
  distinctArrangements: number | null;
  degenerateReason: string | null;
  candidateCountRequested: number | null;
  candidateCountGenerated: number | null;
  plannedRuns: number | null;
  completedRuns: number | null;
  baselineCompletedFullSeedBank: boolean | null;
  rankedComparableCandidates: string[];
};

export type StrategySearchResult = {
  schema: string;
  status: string;
  request: unknown;
  baseline: StrategyCandidateSummary | null;
  candidates: StrategyCandidateSummary[];
  best: StrategyCandidateSummary | null;
  /** Candidate ids the engine ranks above their paired baseline (empty when none). */
  improvements: string[];
  improvement: unknown;
  searchSpace: StrategySearchSpace | null;
  limitations: string[];
  nativeValidationScope: unknown;
};

export type StrategySearchProgress = {
  completed: number | null;
  total: number | null;
  candidateId: string | null;
  /** Lexicographic best score so far; an array while a search is running, or null. */
  bestScore: unknown;
} | null;

export type StrategyJobStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "cancelled"
  | "failed";

export type StrategyJobSnapshot = {
  jobId: string;
  status: StrategyJobStatus;
  request: StrategySearchRequest;
  progress: StrategySearchProgress;
  result: StrategySearchResult | null;
  error: { code: string; message: string } | null;
  createdAt: number | null;
  startedAt: number | null;
  finishedAt: number | null;
  cancellable: boolean;
};

export class StrategySearchApiError extends Error {
  code: string;
  status: number;
  expectedPath?: string;
  problems: string[];
  activeJobId?: string;

  constructor(status: number, payload: Record<string, unknown>) {
    super(
      typeof payload.message === "string" && payload.message
        ? payload.message
        : `strategy search endpoint responded ${status}`,
    );
    this.name = "StrategySearchApiError";
    this.status = status;
    this.code = typeof payload.code === "string" ? payload.code : "search-endpoint-error";
    if (typeof payload.expectedPath === "string") this.expectedPath = payload.expectedPath;
    this.problems = Array.isArray(payload.problems)
      ? payload.problems.filter((entry): entry is string => typeof entry === "string")
      : [];
    if (typeof payload.activeJobId === "string") this.activeJobId = payload.activeJobId;
  }
}

async function requestJson(path: string, init?: RequestInit): Promise<{ status: number; payload: unknown }> {
  const response = await fetch(`${STRATEGY_SEARCH_ENDPOINT}${path}`, init);
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new StrategySearchApiError(response.status, {
        code: "invalid-json",
        message: "the local strategy-search endpoint did not return JSON",
      });
    }
  }
  if (!response.ok) {
    throw new StrategySearchApiError(
      response.status,
      (payload ?? {}) as Record<string, unknown>,
    );
  }
  return { status: response.status, payload };
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Defaults always come from the engine module; nothing is duplicated in the browser. */
export async function fetchStrategySearchDefaults(): Promise<
  StrategySearchDefaults | StrategySearchUnavailable
> {
  try {
    const { payload } = await requestJson("/defaults");
    const record = asRecord(payload);
    const bounds = asRecord(record.bounds);
    return {
      available: true,
      request: asRecord(record.request) as StrategySearchRequest,
      resultSchema:
        typeof record.resultSchema === "string" ? record.resultSchema : STRATEGY_SEARCH_RESULT_SCHEMA,
      bounds: {
        candidateCount: asNumberOrNull(bounds.candidateCount) ?? 64,
        seeds: asNumberOrNull(bounds.seeds) ?? 8,
        tickLimit: asNumberOrNull(bounds.tickLimit) ?? 20000,
        objectives: Array.isArray(bounds.objectives)
          ? bounds.objectives.filter((entry): entry is string => typeof entry === "string")
          : [],
      },
    };
  } catch (error) {
    if (error instanceof StrategySearchApiError) {
      return {
        available: false,
        code: error.code,
        message: error.message,
        ...(error.expectedPath ? { expectedPath: error.expectedPath } : {}),
      };
    }
    throw error;
  }
}

function parseCandidate(raw: unknown): StrategyCandidateSummary | null {
  const record = asRecord(raw);
  if (typeof record.candidateId !== "string") return null;
  return {
    candidateId: record.candidateId,
    kind: typeof record.kind === "string" ? record.kind : null,
    axis: typeof record.axis === "string" ? record.axis : null,
    provisional: typeof record.provisional === "boolean" ? record.provisional : null,
    scoreDefinition: typeof record.scoreDefinition === "string" ? record.scoreDefinition : null,
    unrankedReason: typeof record.unrankedReason === "string" ? record.unrankedReason : null,
    scenarioSha256: typeof record.scenarioSha256 === "string" ? record.scenarioSha256 : null,
    ...(record.scenario !== undefined ? { scenario: record.scenario } : {}),
    score: record.score === undefined ? null : record.score,
    pairedBaselineScore:
      record.pairedBaselineScore === undefined ? null : record.pairedBaselineScore,
    comparable: typeof record.comparable === "boolean" ? record.comparable : null,
    completed: typeof record.completed === "boolean" ? record.completed : null,
    fullSeedBankComplete:
      typeof record.fullSeedBankComplete === "boolean" ? record.fullSeedBankComplete : null,
    beatsBaseline: typeof record.beatsBaseline === "boolean" ? record.beatsBaseline : null,
    metrics: asRecord(record.metrics),
    evaluations: Array.isArray(record.evaluations) ? record.evaluations : [],
    seedsUsed: Array.isArray(record.seedsUsed) ? record.seedsUsed : [],
  };
}

/**
 * Render the engine's lexicographic score (a real array such as [17, 1, -7000]) without ever
 * coercing it to a number. A missing score still renders as the em dash.
 */
export function formatStrategyScore(score: unknown): string {
  if (score === null || score === undefined) return "\u2014";
  if (Array.isArray(score)) {
    return `[${score.map((entry) => (typeof entry === "number" ? String(entry) : JSON.stringify(entry))).join(", ")}]`;
  }
  if (typeof score === "number") return String(score);
  return JSON.stringify(score);
}

/** Read one numeric metric out of a candidate's engine metrics block (null when absent). */
export function strategyCandidateMetric(
  candidate: StrategyCandidateSummary | null | undefined,
  key: string,
): number | null {
  const value = candidate?.metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseAxisReport(raw: unknown): StrategySearchAxisReport | null {
  const record = asRecord(raw);
  if (typeof record.axis !== "string") return null;
  const units = Array.isArray(record.units)
    ? record.units
        .map(asRecord)
        .filter((entry) => typeof entry.unit === "string")
        .map((entry) => ({
          unit: entry.unit as string,
          orderRelevantArrangements: asNumberOrNull(entry.orderRelevantArrangements) ?? 0,
        }))
    : [];
  return {
    axis: record.axis,
    distinctArrangements: asNumberOrNull(record.distinctArrangements),
    arrangements: asNumberOrNull(record.arrangements),
    degenerateReason: typeof record.degenerateReason === "string" ? record.degenerateReason : null,
    units,
  };
}

function parseSearchSpace(raw: unknown): StrategySearchSpace | null {
  if (raw == null) return null;
  const record = asRecord(raw);
  if (Object.keys(record).length === 0) return null;
  return {
    searchAxis: typeof record.searchAxis === "string" ? record.searchAxis : null,
    axis: typeof record.axis === "string" ? record.axis : null,
    axes: Array.isArray(record.axes)
      ? record.axes
          .map(parseAxisReport)
          .filter((entry): entry is StrategySearchAxisReport => Boolean(entry))
      : [],
    distinctArrangements: asNumberOrNull(record.distinctArrangements),
    degenerateReason: typeof record.degenerateReason === "string" ? record.degenerateReason : null,
    candidateCountRequested: asNumberOrNull(record.candidateCountRequested),
    candidateCountGenerated: asNumberOrNull(record.candidateCountGenerated),
    plannedRuns: asNumberOrNull(record.plannedRuns),
    completedRuns: asNumberOrNull(record.completedRuns),
    baselineCompletedFullSeedBank:
      typeof record.baselineCompletedFullSeedBank === "boolean"
        ? record.baselineCompletedFullSeedBank
        : null,
    rankedComparableCandidates: Array.isArray(record.rankedComparableCandidates)
      ? record.rankedComparableCandidates.filter(
          (entry): entry is string => typeof entry === "string",
        )
      : [],
  };
}

/**
 * Parse `ka-strategy-search-result-1` without re-ranking or filling missing numbers. The engine
 * scores lexicographically (an array), so `score`/`pairedBaselineScore` keep their exact array
 * shape, `evaluations`/`seedsUsed` stay run arrays and `completed` stays a boolean. `improvement`
 * keeps whatever shape the engine produced (it can answer `improvement: false`).
 */
export function parseStrategySearchResult(raw: unknown): StrategySearchResult | null {
  const record = asRecord(raw);
  if (typeof record.schema !== "string") return null;
  const candidates = Array.isArray(record.candidates)
    ? record.candidates.map(parseCandidate).filter((entry): entry is StrategyCandidateSummary => Boolean(entry))
    : [];
  return {
    schema: record.schema,
    status: typeof record.status === "string" ? record.status : "unknown",
    request: record.request ?? null,
    baseline: record.baseline == null ? null : parseCandidate(record.baseline),
    candidates,
    best: record.best == null ? null : parseCandidate(record.best),
    improvements: Array.isArray(record.improvements)
      ? record.improvements.filter((entry): entry is string => typeof entry === "string")
      : [],
    improvement: record.improvement ?? null,
    searchSpace: parseSearchSpace(record.searchSpace),
    limitations: Array.isArray(record.limitations)
      ? record.limitations.filter((entry): entry is string => typeof entry === "string")
      : [],
    nativeValidationScope: record.nativeValidationScope ?? null,
  };
}

const JOB_STATUSES: readonly StrategyJobStatus[] = [
  "queued",
  "running",
  "cancelling",
  "completed",
  "cancelled",
  "failed",
];

export function parseJobSnapshot(raw: unknown): StrategyJobSnapshot {
  const record = asRecord(raw);
  const status = JOB_STATUSES.includes(record.status as StrategyJobStatus)
    ? (record.status as StrategyJobStatus)
    : "failed";
  const progressRecord = record.progress == null ? null : asRecord(record.progress);
  const errorRecord = record.error == null ? null : asRecord(record.error);
  return {
    jobId: typeof record.jobId === "string" ? record.jobId : "",
    status,
    request: asRecord(record.request) as StrategySearchRequest,
    progress: progressRecord
      ? {
          completed: asNumberOrNull(progressRecord.completed),
          total: asNumberOrNull(progressRecord.total),
          candidateId:
            typeof progressRecord.candidateId === "string" ? progressRecord.candidateId : null,
          bestScore: progressRecord.bestScore === undefined ? null : progressRecord.bestScore,
        }
      : null,
    result: record.result == null ? null : parseStrategySearchResult(record.result),
    error: errorRecord
      ? {
          code: typeof errorRecord.code === "string" ? errorRecord.code : "search-failed",
          message:
            typeof errorRecord.message === "string" ? errorRecord.message : "the search failed",
        }
      : null,
    createdAt: asNumberOrNull(record.createdAt),
    startedAt: asNumberOrNull(record.startedAt),
    finishedAt: asNumberOrNull(record.finishedAt),
    cancellable: record.cancellable === true,
  };
}

export async function startStrategySearchJob(
  request: StrategySearchRequest,
): Promise<StrategyJobSnapshot> {
  const { payload } = await requestJson("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ request: { schema: STRATEGY_SEARCH_SCHEMA, ...request } }),
  });
  return parseJobSnapshot(payload);
}

export async function fetchStrategySearchJob(jobId: string): Promise<StrategyJobSnapshot> {
  const { payload } = await requestJson(`/jobs/${encodeURIComponent(jobId)}`);
  return parseJobSnapshot(payload);
}

export async function cancelStrategySearchJob(jobId: string): Promise<StrategyJobSnapshot> {
  const { payload } = await requestJson(`/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  return parseJobSnapshot(payload);
}

/**
 * Run one strategy candidate's own scenario through the authoritative runner and store it as the
 * generated battle, so "replay best candidate" uses the exact same path as the battle-setup page.
 * The scenario is sent verbatim: this module never rewrites combat inputs.
 */
export async function replayStrategyScenario(
  scenario: unknown,
  title: string | null,
): Promise<{ ok: true } | { ok: false; message: string }> {
  let response: Response;
  try {
    response = await fetch("/api/battle-run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(scenario),
    });
  } catch (error) {
    return { ok: false, message: `the local runner is unreachable: ${String(error)}` };
  }
  const text = await response.text();
  if (!response.ok) {
    return { ok: false, message: `runner responded ${response.status}: ${text.slice(0, 400)}` };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { ok: false, message: `runner did not return JSON: ${String(error)}` };
  }
  const { result, issues } = parseBattleReplayResult(parsed);
  if (!result) {
    return { ok: false, message: `replay payload rejected: ${issues.join("; ")}` };
  }
  const withWarnings: BattleReplayResult = {
    ...result,
    setupSummary: {
      ...result.setupSummary,
      warnings: [...(result.setupSummary.warnings ?? []), "strategy-search:candidate-scenario"],
    },
  };
  writeGeneratedBattle(withWarnings, undefined, ["strategy-search:candidate-scenario"], title);
  return { ok: true };
}
