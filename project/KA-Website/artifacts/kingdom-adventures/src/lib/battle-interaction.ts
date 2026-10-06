import {
  BATTLE_REPLAY_SCHEMA,
  parseBattleReplayResult,
  type BattleReplayEvent,
  type BattleReplayResult,
} from "@/lib/battle-replay-result";
import { CANONICAL_RECOVERY_ITEMS, canonicalRecoveryItem } from "@/lib/battle-setup";
import type { GeneratedBattleRecord } from "@/lib/generated-battle-store";

/**
 * Interactive consumables during a fight - `ka-battle-interaction-1`.
 *
 * The contract and its evidence are published in
 * `RE-evidence/20260920-visual-battle-builder/INTERACTION-CONTRACT.md`; the server side is
 * `api/battle-run.py` -> `api/_battle_runtime/combat_interaction.py` (mirrored in
 * `KA-Website/tools/recovery/combat_interaction.py`).
 *
 * A click is a request, not an effect: the server re-runs the supplied scenario with one command
 * inserted at the clicked tick and returns the authoritative `ka-battle-replay-1` payload of that
 * branch. This module only builds the request, validates/parses the response and reads the runner's
 * own indicators (tick, stock, preserved prefix). It never computes combat numbers, never applies an
 * effect locally and never edits a replay.
 */

export const BATTLE_INTERACTION_SCHEMA = "ka-battle-interaction-1";
export const BATTLE_INTERACTION_POLL_SCHEMA = "ka-battle-interaction-poll-1";
export const BATTLE_INTERACTION_ENDPOINT = "/api/battle-run";
export const HOLY_HERB_ITEM = "holy_herb";
/**
 * Playback window the client asks for, in replay ticks (20 ticks = one second of playback). The
 * response is the true engine branch through the clicked tick plus this window; the rest of the
 * branch is computed asynchronously and polled. Measured on the representative 6-own/21-enemy fight
 * this keeps the click under a second even at tick 1000 of 6070, and the window is more than the
 * branch job's first stride while the clock keeps playing.
 */
export const BATTLE_INTERACTION_WINDOW_TICKS = 60;
export const BATTLE_INTERACTION_PHASES = ["before_fighters", "after_fighters"] as const;
export type BattleInteractionPhase = (typeof BATTLE_INTERACTION_PHASES)[number];

export type BattleInteractionConsumableType = "holy_herb" | "recovery_item";

export type BattleInteractionConsumable = {
  /** `"holy_herb"` or the exact key the scenario declares in `items` */
  name: string;
  type: BattleInteractionConsumableType;
  /** recovered recovery parameter: 10 HP, 11 MP, 12; null when the row is not recoverable */
  parameter: number | null;
  scope: "all" | "single" | null;
  /** false => the icon must be disabled (`disabledReason` says why) */
  supported: boolean;
  disabledReason: string | null;
  /** declared finite stock in the source scenario */
  declaredStock: number;
  /** canonical Item.txt aliases when the name resolves to a recovered row */
  aliases: string[];
};

export type BattleInteractionStock = {
  item: string;
  declared: number;
  before: number;
  after: number;
  spent: boolean;
};

export type BattleInteractionChange = {
  unitId: string | null;
  parameter: number;
  before: number;
  after: number;
  max: number;
  seq: number;
};

export type BattleInteractionAcceptance = {
  accepted: boolean;
  used: boolean;
  blocked: string | null;
  reason: string;
  parameter: number | null;
  scope: "all" | "single" | null;
  percent: number | null;
  targetUnitIds: string[];
  changed: BattleInteractionChange[];
  stock: BattleInteractionStock;
};

export type BattleInteractionPrefix = {
  commandTick: number;
  eventCount: number;
  lastTick: number | null;
  digest: string;
  digestAlgorithm?: string;
  verify?: string;
  displayedTick: number | null;
};

export type BattleInteractionTickIndicators = {
  commandTick: number;
  phase: string;
  displayedTick: number | null;
  source: string;
  replayTicks: number;
  lastEventTick: number | null;
  verdictTick: number | null;
  finishTick: number | null;
  censored: boolean | null;
  stopReason: string | null;
};

export type BattleInteractionResult = {
  schema: string;
  status: "accepted" | "no-effect";
  command: {
    tick: number;
    phase: string;
    item: string;
    resolved: {
      type: string;
      parameter: number | null;
      scope: string | null;
      supported: boolean;
      declaredStock: number;
    };
    scheduleIndex: number;
    scheduledInputs: Array<Record<string, unknown>>;
  };
  acceptance: BattleInteractionAcceptance;
  prefix: BattleInteractionPrefix;
  indicators: { tick: BattleInteractionTickIndicators; stock: Record<string, unknown> };
  consumables: BattleInteractionConsumable[];
  /** the exact branch scenario the server ran; reuse it as the source of the next click */
  scenario: Record<string, unknown>;
  replay: BattleReplayResult;
  limits: string[];
  /**
   * Present only when the server answered with a live window (`windowTicks`): the branch was
   * simulated through `stopTick` and the rest is computed by one branch job. A windowed result is
   * NOT a finished fight and must be polled (`ka-battle-interaction-poll-1`) until `complete`.
   */
  window?: BattleInteractionWindow;
};

export type BattleInteractionWindow = {
  stopTick: number;
  horizonTick: number;
  remainingTicks: number;
  jobId: string | null;
  state: string | null;
  complete: boolean;
  note?: string;
};

export type BattleInteractionRequestBody = {
  schema: typeof BATTLE_INTERACTION_SCHEMA;
  scenario: Record<string, unknown>;
  command: { kind: "use-item"; tick: number; phase: BattleInteractionPhase; item: string };
  displayedTick?: number;
  expectedPrefixEventCount?: number;
  /** ask for the fast live-window answer; omitted => the unchanged full branch response */
  windowTicks?: number;
};

export type BattleInteractionPollBody = {
  schema: typeof BATTLE_INTERACTION_POLL_SCHEMA;
  jobId: string;
  /** the first tick the caller does not have; the reply returns events at or after it */
  fromTick: number;
};

export type BattleInteractionPollReply = {
  schema: string;
  jobId: string;
  /** "running" | "ready" | "failed" | "superseded" - anything but "ready" is not the whole branch */
  state: string;
  fromTick: number | null;
  /** the job's own event count below `fromTick`: the splice guard */
  prefixEventCount: number;
  window: {
    stopTick: number | null;
    horizonTick: number | null;
    remainingTicks: number | null;
    complete: boolean;
  };
  /** the branch replay with `events` carrying only the ticks at or after `fromTick` */
  replay: BattleReplayResult;
  limits: string[];
};

export type BattleInteractionIssue = { code: string; message: string };

export type BattleInteractionOutcome =
  | { ok: true; result: BattleInteractionResult }
  | {
      ok: false;
      code: string;
      status: number;
      message: string;
      issues: string[];
      acceptance?: BattleInteractionAcceptance;
    };

export type BattleInteractionHttpResult = { ok: boolean; status: number; text: string };
export type BattleInteractionTransport = (
  body: BattleInteractionRequestBody,
) => Promise<BattleInteractionHttpResult>;

export class BattleInteractionTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BattleInteractionTransportError";
  }
}

const SINGLE_SCOPE_REASON =
  "single-resident scope is not reachable in battle: native item use dispatches ExecuteItem 0x167b834 " +
  "with a null single target, so only the all-residents recovery branch (0x167aea0) is reachable";
const UNSUPPORTED_ROW_REASON =
  "the declared row is outside the recovered recovery-item classification (bonusCategory 3, bonusType 0..5)";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asInt(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

/** The scenario's declared consumables, from a scenario object and/or a stored replay. */
export function interactionCapabilities(input: {
  scenario?: unknown;
  replay?: BattleReplayResult | null;
}): BattleInteractionConsumable[] {
  const scenario = isRecord(input.scenario) ? input.scenario : null;
  const summary = input.replay?.setupSummary;
  const items = isRecord(scenario?.items)
    ? (scenario?.items as Record<string, unknown>)
    : (summary?.items as Record<string, unknown> | undefined) ?? {};
  const itemStock = isRecord(scenario?.itemStock)
    ? (scenario?.itemStock as Record<string, number>)
    : summary?.itemStock ?? {};
  const herbStock = asInt(scenario?.holyHerbStock) ?? summary?.holyHerbStock ?? 0;

  const rows: BattleInteractionConsumable[] = [
    {
      name: HOLY_HERB_ITEM,
      type: "holy_herb",
      parameter: 11,
      scope: "all",
      supported: true,
      disabledReason: null,
      declaredStock: herbStock,
      aliases: canonicalRecoveryItem("Holy Herb")?.aliases ?? [],
    },
  ];
  for (const name of Object.keys(items).sort()) {
    const row = items[name];
    const canonical = canonicalRecoveryItem(name);
    const bonusType = isRecord(row) ? asInt(row.bonusType) : null;
    const parameter = canonical?.parameter ?? (bonusType !== null ? [10, 10, 11, 11, 12, 12][bonusType] ?? null : null);
    const scope = canonical?.scope ?? (bonusType !== null && bonusType % 2 === 0 ? "all" : "single");
    const supported = scope === "all" && parameter !== null;
    rows.push({
      name,
      type: "recovery_item",
      parameter,
      scope,
      supported,
      disabledReason: supported ? null : scope === "single" ? SINGLE_SCOPE_REASON : UNSUPPORTED_ROW_REASON,
      declaredStock: itemStock[name] ?? 0,
      aliases: canonical?.aliases ?? [],
    });
  }
  return rows;
}

/** Everything that depends on the stock the fight has already spent. */
export type BattleInteractionAvailability = { usable: boolean; reason: string | null; remaining: number };

export function consumableAvailability(
  item: BattleInteractionConsumable,
  remaining: number,
): BattleInteractionAvailability {
  if (!item.supported) return { usable: false, reason: item.disabledReason, remaining };
  if (remaining <= 0) return { usable: false, reason: "no stock left at the displayed tick", remaining };
  return { usable: true, reason: null, remaining };
}

/** The runner's own end-of-run totals (never a mid-fight value). */
export function remainingConsumableStock(replay: BattleReplayResult, item: string): number {
  if (item === HOLY_HERB_ITEM) return replay.holyHerbRemaining ?? 0;
  return replay.itemRemaining?.[item] ?? replay.setupSummary.itemStock?.[item] ?? 0;
}

function declaredStockOf(replay: BattleReplayResult, item: string): number {
  if (item === HOLY_HERB_ITEM) return replay.setupSummary.holyHerbStock ?? 0;
  return replay.setupSummary.itemStock?.[item] ?? 0;
}

/**
 * Stock the displayed fight still has at `tick`, read from the replay's own runner records.
 *
 * Seed: `setupSummary.holyHerbStock` / `setupSummary.itemStock`. Then the last `battle_item` event for
 * this item at or before `tick` carries the runner's post-attempt `remaining` (a use spends only when
 * the runner reports `used`). `replay.holyHerbRemaining` / `itemRemaining` are end-of-run totals.
 */
export function stockAtTick(replay: BattleReplayResult, item: string, tick: number): number {
  let remaining = declaredStockOf(replay, item);
  for (const event of replay.events as BattleReplayEvent[]) {
    if (event.kind !== "battle_item" || event.item !== item) continue;
    if (typeof event.tick === "number" && event.tick <= tick && typeof event.remaining === "number") {
      remaining = event.remaining;
    }
  }
  return remaining;
}

/** The tick of one replay event, or null when the sequence does not exist. */
export function interactionTickForEvent(replay: BattleReplayResult, seq: number): number | null {
  const events = replay.events as BattleReplayEvent[];
  const direct = events[seq];
  if (direct && direct.seq === seq) return direct.tick;
  const found = events.find((event) => event.seq === seq);
  return found ? found.tick : null;
}

/** The tick/stock sources the UI reads; the same fields the server reports in `indicators`. */
export function replayTickIndicators(replay: BattleReplayResult): BattleInteractionTickIndicators {
  const events = replay.events as BattleReplayEvent[];
  const finalState = replay.finalState as Record<string, unknown>;
  return {
    commandTick: events.length > 0 ? events[events.length - 1].tick : 0,
    phase: "display",
    displayedTick: null,
    source: "replay.events[].tick (events are ordered by seq; tick -1 is initialization)",
    replayTicks: replay.ticks,
    lastEventTick: events.length > 0 ? Math.max(...events.map((event) => event.tick)) : null,
    verdictTick: (finalState.verdictTick as number | null) ?? null,
    finishTick: (finalState.finishTick as number | null) ?? null,
    censored: (finalState.censored as boolean | null) ?? null,
    stopReason: (finalState.stopReason as string | null) ?? null,
  };
}

/** How many leading events of `displayed` and `branch` are identical (the trust check). */
export function preservedPrefixLength(
  displayed: BattleReplayResult,
  branch: BattleReplayResult,
  commandTick: number,
): number {
  const left = (displayed.events as BattleReplayEvent[]).filter((event) => event.tick < commandTick);
  const right = (branch.events as BattleReplayEvent[]).filter((event) => event.tick < commandTick);
  const limit = Math.min(left.length, right.length);
  let matched = 0;
  while (matched < limit && stableJson(left[matched]) === stableJson(right[matched])) matched += 1;
  return matched;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, entry]) => `${JSON.stringify(key)}:${stableJson(entry)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function prefixCount(replay: BattleReplayResult, tick: number): number {
  return (replay.events as BattleReplayEvent[]).filter((event) => event.tick < tick).length;
}

/** Build the request body, including the displayed-prefix guard when a displayed replay is supplied. */
export function buildBattleInteractionRequest(input: {
  scenario: unknown;
  item: string;
  tick: number;
  phase?: BattleInteractionPhase;
  displayedReplay?: BattleReplayResult | null;
  /** opt in to the fast live-window answer (see BATTLE_INTERACTION_WINDOW_TICKS) */
  windowTicks?: number | null;
}):
  | { ok: true; body: BattleInteractionRequestBody }
  | { ok: false; issues: BattleInteractionIssue[] } {
  const issues: BattleInteractionIssue[] = [];
  if (!isRecord(input.scenario)) {
    issues.push({ code: "invalid-scenario", message: "scenario must be the ka-special-combat-research-1 object" });
  } else if (input.scenario.schema !== "ka-special-combat-research-1") {
    issues.push({ code: "invalid-scenario", message: `scenario.schema is ${String(input.scenario.schema)}` });
  }
  if (!Number.isInteger(input.tick) || input.tick < 0) {
    issues.push({ code: "invalid-tick", message: "tick must be the integer tick of a replay event" });
  }
  if (typeof input.item !== "string" || input.item.length === 0) {
    issues.push({ code: "invalid-item", message: "item must name a consumable" });
  }
  const phase = input.phase ?? "before_fighters";
  if (!BATTLE_INTERACTION_PHASES.includes(phase)) {
    issues.push({ code: "invalid-phase", message: `phase must be one of ${BATTLE_INTERACTION_PHASES.join(", ")}` });
  }
  if (issues.length > 0) return { ok: false, issues };
  const body: BattleInteractionRequestBody = {
    schema: BATTLE_INTERACTION_SCHEMA,
    scenario: input.scenario as Record<string, unknown>,
    command: { kind: "use-item", tick: input.tick, phase, item: input.item },
    displayedTick: input.tick,
    ...(input.displayedReplay
      ? { expectedPrefixEventCount: prefixCount(input.displayedReplay, input.tick) }
      : {}),
    ...(Number.isInteger(input.windowTicks) && (input.windowTicks ?? 0) > 0
      ? { windowTicks: input.windowTicks as number }
      : {}),
  };
  return { ok: true, body };
}

/** HTTP transport: the body is returned for every status so a refusal keeps its acceptance report. */
export function createHttpBattleInteractionTransport(
  endpoint: string = BATTLE_INTERACTION_ENDPOINT,
  fetchImpl: typeof fetch = fetch,
): BattleInteractionTransport {
  return async (body) => {
    const response = await fetchImpl(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: response.ok, status: response.status, text: await response.text() };
  };
}

/** In-memory transport for checks and embedded callers. */
export function inlineBattleInteractionTransport(
  handler: (body: BattleInteractionRequestBody) => BattleInteractionHttpResult | Promise<BattleInteractionHttpResult>,
): BattleInteractionTransport {
  return async (body) => handler(body);
}

export type BattleInteractionPollTransport = (
  body: BattleInteractionPollBody,
) => Promise<BattleInteractionHttpResult>;

/** HTTP transport for the branch-job poll; same endpoint, separate envelope. */
export function createHttpBattleInteractionPollTransport(
  endpoint: string = BATTLE_INTERACTION_ENDPOINT,
  fetchImpl: typeof fetch = fetch,
): BattleInteractionPollTransport {
  return async (body) => {
    const response = await fetchImpl(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: response.ok, status: response.status, text: await response.text() };
  };
}

/** In-memory poll transport for checks and embedded callers. */
export function inlineBattleInteractionPollTransport(
  handler: (body: BattleInteractionPollBody) => BattleInteractionHttpResult | Promise<BattleInteractionHttpResult>,
): BattleInteractionPollTransport {
  return async (body) => handler(body);
}

function parseWindow(value: unknown): BattleInteractionWindow | undefined {
  if (!isRecord(value)) return undefined;
  const stopTick = asInt(value.stopTick);
  const horizonTick = asInt(value.horizonTick);
  const remainingTicks = asInt(value.remainingTicks);
  if (stopTick === null || horizonTick === null || remainingTicks === null) return undefined;
  return {
    stopTick,
    horizonTick,
    remainingTicks,
    jobId: typeof value.jobId === "string" ? value.jobId : null,
    state: typeof value.state === "string" ? value.state : null,
    complete: value.complete === true,
    ...(typeof value.note === "string" ? { note: value.note } : {}),
  };
}

/** Parse one branch-job poll reply. Nothing is lenient about the splice guard fields. */
export function parseBattleInteractionPoll(value: unknown): {
  reply?: BattleInteractionPollReply;
  issues: string[];
} {
  const issues: string[] = [];
  if (!isRecord(value)) return { issues: ["poll reply is not an object"] };
  if (value.schema !== BATTLE_INTERACTION_POLL_SCHEMA) issues.push(`schema is ${String(value.schema)}`);
  if (typeof value.jobId !== "string" || value.jobId.length === 0) issues.push("jobId is missing");
  if (typeof value.state !== "string") issues.push("state is missing");
  const prefixEventCount = asInt(value.prefixEventCount);
  if (prefixEventCount === null || prefixEventCount < 0) issues.push("prefixEventCount is missing");
  const window = isRecord(value.window) ? value.window : null;
  if (!window) issues.push("window is missing");
  const { result: replay, issues: replayIssues } = parseBattleReplayResult(value.replay);
  if (!replay) issues.push(`replay rejected: ${replayIssues.join("; ")}`);
  if (issues.length > 0 || !replay || !window) {
    return { issues: issues.length > 0 ? issues : ["poll reply is incomplete"] };
  }
  return {
    issues: [],
    reply: {
      schema: BATTLE_INTERACTION_POLL_SCHEMA,
      jobId: value.jobId as string,
      state: value.state as string,
      fromTick: asInt(value.fromTick),
      prefixEventCount: prefixEventCount as number,
      window: {
        stopTick: asInt(window.stopTick),
        horizonTick: asInt(window.horizonTick),
        remainingTicks: asInt(window.remainingTicks),
        complete: window.complete === true,
      },
      replay,
      limits: asStringArray(value.limits),
    },
  };
}

/** Send one branch-job poll and parse it; refusals (busy/pending/unknown) come back as `ok:false`. */
export async function requestBattleBranchPoll(
  body: BattleInteractionPollBody,
  transport: BattleInteractionPollTransport,
): Promise<{ ok: true; reply: BattleInteractionPollReply } | { ok: false; code: string; status: number; message: string }> {
  let response: BattleInteractionHttpResult;
  try {
    response = await transport(body);
  } catch (error) {
    return { ok: false, code: "transport-failed", status: 0, message: `branch poll failed: ${String(error)}` };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(response.text);
  } catch (error) {
    return { ok: false, code: "invalid-response", status: response.status, message: `not JSON: ${String(error)}` };
  }
  const errorBody = parseBattleInteractionError(parsed);
  if (errorBody && (!response.ok || errorBody.status >= 400)) {
    return { ok: false, code: errorBody.code, status: errorBody.status || response.status, message: errorBody.message };
  }
  const { reply, issues } = parseBattleInteractionPoll(parsed);
  if (!reply) {
    return { ok: false, code: "invalid-response", status: response.status, message: `poll rejected: ${issues.join("; ")}` };
  }
  return { ok: true, reply };
}

/** The first tick a displayed replay does not have: the poll's `fromTick` boundary. */
export function interactionFromTick(replay: BattleReplayResult): number {
  const windowed = asInt((replay.finalState as Record<string, unknown>).windowStopTick);
  const lastEventTick = (replay.events as BattleReplayEvent[]).reduce(
    (highest, event) => (typeof event.tick === "number" && event.tick > highest ? event.tick : highest),
    -1,
  );
  return Math.max(windowed ?? -1, lastEventTick) + 1;
}

/**
 * Splice one poll extension into the displayed branch. The extension is only accepted when its own
 * `prefixEventCount` matches the caller's event count below the same boundary, so a splice is always
 * an exact prefix match; the horizon may only grow, and nothing else is recomputed here.
 */
export function mergeBranchExtension(
  current: BattleReplayResult,
  delta: { replay: BattleReplayResult; prefixEventCount: number; fromTick: number },
): { ok: true; replay: BattleReplayResult } | { ok: false; reason: string } {
  const have = (current.events as BattleReplayEvent[]).filter((event) => event.tick < delta.fromTick).length;
  if (have !== delta.prefixEventCount) {
    return {
      ok: false,
      reason:
        `the extension does not continue this branch: ${delta.prefixEventCount} events before tick ` +
        `${delta.fromTick} against ${have} displayed`,
    };
  }
  const currentEnd = interactionFromTick(current) - 1;
  const extensionEnd = interactionFromTick(delta.replay) - 1;
  if (extensionEnd < currentEnd) {
    return { ok: false, reason: `the extension ends at tick ${extensionEnd}, before the displayed ${currentEnd}` };
  }
  return {
    ok: true,
    replay: {
      ...delta.replay,
      events: [
        ...(current.events as BattleReplayEvent[]).filter((event) => event.tick < delta.fromTick),
        ...(delta.replay.events as BattleReplayEvent[]).filter((event) => event.tick >= delta.fromTick),
      ],
    } as BattleReplayResult,
  };
}

/**
 * The synchronous decision a consumable click makes, before any request exists: which tick is
 * clicked, whether playback must be restored after the branch commits, and why a click is refused.
 * Pure so the freeze/guard behaviour is checkable without a browser.
 */
export function planConsumableClick(input: {
  frames: ReadonlyArray<{ tick: number }>;
  step: number;
  playing: boolean;
  busy: string | null;
  /** the fight is already over (verdict tick reached) */
  ended?: boolean;
  /** the branch source could not be read */
  sourceIssue?: string | null;
}): { ok: true; tick: number; resumePlaying: boolean } | { ok: false; reason: string } {
  if (input.sourceIssue) return { ok: false, reason: input.sourceIssue };
  if (input.busy) {
    return { ok: false, reason: `still applying the previous item (${input.busy}); the click was not sent` };
  }
  if (input.ended) return { ok: false, reason: "the battle has ended." };
  const frame = input.frames[Math.min(Math.max(input.step, 0), Math.max(input.frames.length - 1, 0))];
  if (!frame) return { ok: false, reason: "no replay frame is displayed yet" };
  return { ok: true, tick: frame.tick, resumePlaying: input.playing };
}

function parseAcceptance(value: unknown): BattleInteractionAcceptance | null {
  if (!isRecord(value)) return null;
  const stock = isRecord(value.stock) ? value.stock : null;
  if (!stock || typeof stock.item !== "string" || typeof stock.after !== "number") return null;
  return {
    accepted: value.accepted === true,
    used: value.used === true,
    blocked: typeof value.blocked === "string" ? value.blocked : null,
    reason: typeof value.reason === "string" ? value.reason : "",
    parameter: asInt(value.parameter),
    scope: value.scope === "all" || value.scope === "single" ? value.scope : null,
    percent: asInt(value.percent),
    targetUnitIds: asStringArray(value.targetUnitIds),
    changed: (Array.isArray(value.changed) ? value.changed : [])
      .filter((entry): entry is Record<string, unknown> => isRecord(entry))
      .map((entry) => ({
        unitId: typeof entry.unitId === "string" ? entry.unitId : null,
        parameter: asInt(entry.parameter) ?? 0,
        before: asInt(entry.before) ?? 0,
        after: asInt(entry.after) ?? 0,
        max: asInt(entry.max) ?? 0,
        seq: asInt(entry.seq) ?? -1,
      })),
    stock: {
      item: stock.item,
      declared: asInt(stock.declared) ?? 0,
      before: asInt(stock.before) ?? asInt(stock.after) ?? 0,
      after: asInt(stock.after) ?? 0,
      spent: stock.spent === true,
    },
  };
}

export function parseBattleInteractionResponse(value: unknown): {
  result?: BattleInteractionResult;
  issues: string[];
} {
  const issues: string[] = [];
  if (!isRecord(value)) return { issues: ["interaction response is not an object"] };
  if (value.schema !== BATTLE_INTERACTION_SCHEMA) issues.push(`schema is ${String(value.schema)}`);
  if (value.status !== "accepted" && value.status !== "no-effect") issues.push(`status is ${String(value.status)}`);
  const command = isRecord(value.command) ? value.command : null;
  if (!command) issues.push("command is missing");
  else {
    if (typeof command.item !== "string") issues.push("command.item is missing");
    if (asInt(command.tick) === null) issues.push("command.tick is missing");
  }
  const acceptance = parseAcceptance(value.acceptance);
  if (!acceptance) issues.push("acceptance is incomplete");
  const prefix = isRecord(value.prefix) ? value.prefix : null;
  if (!prefix) issues.push("prefix is missing");
  else {
    if (asInt(prefix.commandTick) === null) issues.push("prefix.commandTick is missing");
    if (asInt(prefix.eventCount) === null) issues.push("prefix.eventCount is missing");
  }
  const indicators = isRecord(value.indicators) ? value.indicators : null;
  if (!indicators || !isRecord(indicators.tick) || !isRecord(indicators.stock)) {
    issues.push("indicators.tick/stock are missing");
  }
  if (!isRecord(value.scenario)) issues.push("scenario is missing (the branch source for the next click)");
  const { result: replay, issues: replayIssues } = parseBattleReplayResult(value.replay);
  if (!replay) issues.push(`replay rejected: ${replayIssues.join("; ")}`);
  else if (replay.schema !== BATTLE_REPLAY_SCHEMA) issues.push(`replay schema is ${replay.schema}`);
  if (issues.length > 0 || !replay || !acceptance || !prefix || !command || !indicators) {
    return { issues: issues.length > 0 ? issues : ["interaction response is incomplete"] };
  }
  const window = parseWindow(value.window);
  if (window && !window.complete && window.remainingTicks > 0 && !window.jobId) {
    // A windowed answer must name the job that will finish the branch: without it the client would
    // be left holding a truncated fight it cannot extend.
    issues.push("windowed response carries no jobId");
  }
  if (issues.length > 0) return { issues };
  return {
    issues: [],
    result: {
      schema: BATTLE_INTERACTION_SCHEMA,
      status: value.status === "accepted" ? "accepted" : "no-effect",
      command: {
        tick: command.tick as number,
        phase: typeof command.phase === "string" ? command.phase : "before_fighters",
        item: command.item as string,
        resolved: isRecord(command.resolved)
          ? {
              type: typeof command.resolved.type === "string" ? command.resolved.type : "",
              parameter: asInt(command.resolved.parameter),
              scope: typeof command.resolved.scope === "string" ? command.resolved.scope : null,
              supported: command.resolved.supported === true,
              declaredStock: asInt(command.resolved.declaredStock) ?? 0,
            }
          : { type: "", parameter: null, scope: null, supported: false, declaredStock: 0 },
        scheduleIndex: asInt(command.scheduleIndex) ?? -1,
        scheduledInputs: Array.isArray(command.scheduledInputs)
          ? command.scheduledInputs.filter(isRecord)
          : [],
      },
      acceptance,
      prefix: {
        commandTick: prefix.commandTick as number,
        eventCount: prefix.eventCount as number,
        lastTick: asInt(prefix.lastTick),
        digest: typeof prefix.digest === "string" ? prefix.digest : "",
        digestAlgorithm: typeof prefix.digestAlgorithm === "string" ? prefix.digestAlgorithm : undefined,
        verify: typeof prefix.verify === "string" ? prefix.verify : undefined,
        displayedTick: asInt(prefix.displayedTick),
      },
      indicators: {
        tick: {
          commandTick: asInt((indicators.tick as Record<string, unknown>).commandTick) ?? 0,
          phase: String((indicators.tick as Record<string, unknown>).phase ?? ""),
          displayedTick: asInt((indicators.tick as Record<string, unknown>).displayedTick),
          source: String((indicators.tick as Record<string, unknown>).source ?? ""),
          replayTicks: asInt((indicators.tick as Record<string, unknown>).replayTicks) ?? replay.ticks,
          lastEventTick: asInt((indicators.tick as Record<string, unknown>).lastEventTick),
          verdictTick: asInt((indicators.tick as Record<string, unknown>).verdictTick),
          finishTick: asInt((indicators.tick as Record<string, unknown>).finishTick),
          censored: (indicators.tick as Record<string, unknown>).censored === true,
          stopReason:
            typeof (indicators.tick as Record<string, unknown>).stopReason === "string"
              ? ((indicators.tick as Record<string, unknown>).stopReason as string)
              : null,
        },
        stock: indicators.stock as Record<string, unknown>,
      },
      consumables: (Array.isArray(value.consumables) ? value.consumables : []).filter(isRecord).map((row) => ({
        name: String(row.name ?? ""),
        type: row.type === "holy_herb" ? "holy_herb" : "recovery_item",
        parameter: asInt(row.parameter),
        scope: row.scope === "all" || row.scope === "single" ? row.scope : null,
        supported: row.supported === true,
        disabledReason: typeof row.disabledReason === "string" ? row.disabledReason : null,
        declaredStock: asInt(row.declaredStock) ?? 0,
        aliases: asStringArray(row.aliases),
      })),
      scenario: value.scenario as Record<string, unknown>,
      replay,
      limits: asStringArray(value.limits),
      ...(window ? { window } : {}),
    },
  };
}

export type BattleInteractionErrorBody = {
  status: number;
  code: string;
  message: string;
  acceptance?: BattleInteractionAcceptance;
};

export function parseBattleInteractionError(value: unknown): BattleInteractionErrorBody | null {
  if (!isRecord(value) || value.schema !== "ka-battle-run-error-1") return null;
  return {
    status: asInt(value.status) ?? 0,
    code: typeof value.code === "string" ? value.code : "unknown",
    message: typeof value.message === "string" ? value.message : "",
    ...(parseAcceptance(value.acceptance) ? { acceptance: parseAcceptance(value.acceptance) as BattleInteractionAcceptance } : {}),
  };
}

/**
 * Send one interaction request and parse the outcome. Refusals come back as `ok: false` with the
 * server's own code (and the acceptance report the runner produced, when there is one).
 */
export async function requestBattleInteraction(
  body: BattleInteractionRequestBody,
  transport: BattleInteractionTransport,
): Promise<BattleInteractionOutcome> {
  let response: BattleInteractionHttpResult;
  try {
    response = await transport(body);
  } catch (error) {
    return {
      ok: false,
      code: "transport-failed",
      status: 0,
      message: `interaction transport failed: ${String(error)}`,
      issues: [],
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(response.text);
  } catch (error) {
    return {
      ok: false,
      code: "invalid-response",
      status: response.status,
      message: `the interaction endpoint did not return JSON: ${String(error)}`,
      issues: [],
    };
  }
  const errorBody = parseBattleInteractionError(parsed);
  if (errorBody && (!response.ok || errorBody.status >= 400)) {
    return {
      ok: false,
      code: errorBody.code,
      status: errorBody.status || response.status,
      message: errorBody.message,
      issues: [],
      ...(errorBody.acceptance ? { acceptance: errorBody.acceptance } : {}),
    };
  }
  const { result, issues } = parseBattleInteractionResponse(parsed);
  if (!result) {
    return {
      ok: false,
      code: "invalid-response",
      status: response.status,
      message: `interaction payload rejected: ${issues.join("; ")}`,
      issues,
    };
  }
  return { ok: true, result };
}

/** The scenario source of a stored battle, or the reason the displayed fight cannot be branched. */
export function interactionScenarioFromRecord(
  record: GeneratedBattleRecord | null | undefined,
): { ok: true; scenario: Record<string, unknown> } | { ok: false; reason: string } {
  if (!record) return { ok: false, reason: "no generated battle is stored" };
  const raw = record.scenarioJson;
  if (typeof raw !== "string" || raw.length === 0) {
    return { ok: false, reason: "the stored battle has no source scenario; re-run the setup to enable consumables" };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    return { ok: false, reason: `the stored source scenario is not JSON: ${String(error)}` };
  }
  if (!isRecord(parsed)) return { ok: false, reason: "the stored source scenario is not an object" };
  if (parsed.schema !== "ka-special-combat-research-1") {
    return { ok: false, reason: `the stored source scenario has schema ${String(parsed.schema)}` };
  }
  return { ok: true, scenario: parsed };
}

/** After an accepted interaction: the branch source for the next click. */
export function nextInteractionSource(
  result: BattleInteractionResult,
): { scenario: Record<string, unknown>; replay: BattleReplayResult } {
  return { scenario: result.scenario, replay: result.replay };
}

export { CANONICAL_RECOVERY_ITEMS };
