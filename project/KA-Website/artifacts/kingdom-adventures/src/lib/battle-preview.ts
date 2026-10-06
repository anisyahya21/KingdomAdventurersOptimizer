/**
 * Frontend contract for the read-only formation preview (`POST /api/battle-run`, envelope
 * `ka-battle-preview-1`).
 *
 * The preview NEVER simulates combat and NEVER places anything itself. It hands the existing
 * adapter scenario (`battleSetupToCombatScenario`) to the backend, which runs the authoritative
 * load/prepare pass (`api/_battle_preview.py`) and returns the formation it computed. The preview
 * envelope caps only tickLimit at the API transport maximum; preparation never runs those ticks.
 * Errors keep
 * the existing `message` field used by the battle-run endpoint.
 *
 * The contract mirrors the backend projection exactly:
 *   * parameter entries are `{ effectiveValue, effectiveMaximum }` (the prepared pair, not the
 *     raw inputs), read from both ally and enemy units;
 *   * `petLimits[].maxPets` is `null` whenever the backend has no verified capacity - `null` is
 *     preserved as `null` and never coerced to `NaN` or 0;
 *   * `diagnostics` are `{ code, message }` objects, kept separate from the human-facing messages.
 *
 * This module only builds the envelope, sends it, and validates the response shape. It does not
 * invent capacity rules: `petLimits` is whatever the backend reports.
 */

import { syntheticLegalDesktopApi } from "@/lib/synthetic-legal-desktop";

export const BATTLE_PREVIEW_SCHEMA = "ka-battle-preview-1";

/** Existing endpoint shared with the authoritative runner; the schema discriminates the envelope. */
export const BATTLE_RUN_ENDPOINT = "/api/battle-run";
/** Transport limit for preview envelopes. The preview does not simulate this many ticks. */
export const BATTLE_PREVIEW_MAX_TICK_LIMIT = 10_000;

/** One prepared parameter pair exactly as the backend preview exposes it. */
export type BattlePreviewParameter = {
  /** `effectiveValue` from the preparation pass; absence stays absent and is never defaulted. */
  effectiveValue?: number;
  /** `effectiveMaximum` from the preparation pass; absence stays absent and is never defaulted. */
  effectiveMaximum?: number;
};

export type BattlePreviewUnit = {
  unitId: string | null;
  name: string;
  /** `ally` for the selected roster and their owner-bound pets, `enemy` for the encounter roster. */
  side: string;
  kind: string;
  rosterIndex: number | null;
  cell: [number, number] | null;
  boss: boolean;
  /** Present only on owner-bound household pets; `null` for every other unit. */
  petOwnerName: string | null;
  parameters: Record<string, BattlePreviewParameter>;
};

export type BattlePreviewPetLimit = {
  owner: string;
  /**
   * Backend-reported capacity, or `null` when the backend cannot assert one. `null` distinguishes
   * "unknown" from a real `0` and is preserved verbatim (never `NaN`, never a guessed number).
   */
  maxPets: number | null;
  attachedPets: number | null;
};

export type BattlePreviewDiagnostic = {
  code: string;
  message: string;
};

export type BattlePreview = {
  schema: typeof BATTLE_PREVIEW_SCHEMA;
  units: BattlePreviewUnit[];
  petLimits: BattlePreviewPetLimit[];
  diagnostics: BattlePreviewDiagnostic[];
};

export type BattlePreviewRequest = {
  schema: typeof BATTLE_PREVIEW_SCHEMA;
  scenario: unknown;
};

export class BattlePreviewError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BattlePreviewError";
  }
}

/** Build the exact request body for the preview envelope. */
export function buildBattlePreviewRequest(scenario: unknown): BattlePreviewRequest {
  const previewScenario = isRecord(scenario) && typeof scenario.tickLimit === "number" &&
    scenario.tickLimit > BATTLE_PREVIEW_MAX_TICK_LIMIT
    ? { ...scenario, tickLimit: BATTLE_PREVIEW_MAX_TICK_LIMIT }
    : scenario;
  return { schema: BATTLE_PREVIEW_SCHEMA, scenario: previewScenario };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function integerOrNull(value: unknown): number | null {
  const number = finiteNumberOrNull(value);
  return number === null ? null : Math.trunc(number);
}

function parseCell(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const [x, y] = value;
  if (typeof x !== "number" || typeof y !== "number") return null;
  return [Math.trunc(x), Math.trunc(y)];
}

/**
 * The displayed numbers for one parameter. `null` means the backend did not send that half of the
 * pair; callers must render `null` as unknown rather than substituting 0.
 */
export function battlePreviewParameterNumbers(
  parameter: BattlePreviewParameter | undefined,
): { value: number | null; maximum: number | null } {
  return {
    value: finiteNumberOrNull(parameter?.effectiveValue),
    maximum: finiteNumberOrNull(parameter?.effectiveMaximum),
  };
}

function parseParameter(value: unknown): BattlePreviewParameter | null {
  if (!isRecord(value)) return null;
  const entry: BattlePreviewParameter = {};
  const effectiveValue = finiteNumberOrNull(value.effectiveValue);
  const effectiveMaximum = finiteNumberOrNull(value.effectiveMaximum);
  if (effectiveValue !== null) entry.effectiveValue = effectiveValue;
  if (effectiveMaximum !== null) entry.effectiveMaximum = effectiveMaximum;
  return entry;
}

function parseParameters(value: unknown): Record<string, BattlePreviewParameter> {
  if (!isRecord(value)) return {};
  const parameters: Record<string, BattlePreviewParameter> = {};
  for (const [key, entry] of Object.entries(value)) {
    const parsed = parseParameter(entry);
    if (parsed) parameters[key] = parsed;
  }
  return parameters;
}

/** Validate one backend unit; unknown extra fields are preserved, missing required ones are reported. */
function parseUnit(value: unknown, index: number, issues: string[]): BattlePreviewUnit | null {
  if (!isRecord(value)) {
    issues.push(`units[${index}] is not an object`);
    return null;
  }
  const name = value.name;
  const side = value.side;
  const kind = value.kind;
  if (typeof name !== "string" || !name) {
    issues.push(`units[${index}].name is missing`);
    return null;
  }
  if (typeof side !== "string" || !side) {
    issues.push(`units[${index}].side is missing`);
    return null;
  }
  if (typeof kind !== "string" || !kind) {
    issues.push(`units[${index}].kind is missing`);
    return null;
  }
  return {
    unitId: typeof value.unitId === "string" ? value.unitId : null,
    name,
    side,
    kind,
    rosterIndex: integerOrNull(value.rosterIndex),
    cell: parseCell(value.cell),
    boss: value.boss === true,
    petOwnerName: typeof value.petOwnerName === "string" ? value.petOwnerName : null,
    parameters: parseParameters(value.parameters),
  };
}

function parsePetLimit(value: unknown, index: number, issues: string[]): BattlePreviewPetLimit | null {
  if (!isRecord(value)) {
    issues.push(`petLimits[${index}] is not an object`);
    return null;
  }
  const owner = value.owner;
  if (typeof owner !== "string" || !owner) {
    issues.push(`petLimits[${index}].owner is missing`);
    return null;
  }
  // `maxPets: null` is a real backend answer ("no verified capacity"): keep it null, never NaN.
  return {
    owner,
    maxPets: integerOrNull(value.maxPets),
    attachedPets: integerOrNull(value.attachedPets),
  };
}

/** Diagnostics are advisory: a malformed entry is coerced into a labelled message, not rejected. */
function parseDiagnostic(value: unknown): BattlePreviewDiagnostic {
  if (typeof value === "string") return { code: "diagnostic", message: value };
  if (isRecord(value)) {
    const code = typeof value.code === "string" && value.code ? value.code : "diagnostic";
    const message = typeof value.message === "string" && value.message ? value.message : JSON.stringify(value);
    return { code, message };
  }
  return { code: "diagnostic", message: JSON.stringify(value ?? null) };
}

/**
 * Parse a `ka-battle-preview-1` response. Returns `preview: null` plus human-readable messages when
 * the payload is not a usable preview; the caller shows those instead of a formation.
 */
export function parseBattlePreview(payload: unknown): { preview: BattlePreview | null; issues: string[] } {
  const issues: string[] = [];
  if (!isRecord(payload)) {
    return { preview: null, issues: ["the preview response is not an object"] };
  }
  if (payload.schema !== BATTLE_PREVIEW_SCHEMA) {
    return {
      preview: null,
      issues: [`unexpected preview schema ${JSON.stringify(payload.schema)}; expected ${BATTLE_PREVIEW_SCHEMA}`],
    };
  }
  const rawUnits = Array.isArray(payload.units) ? payload.units : [];
  const units: BattlePreviewUnit[] = [];
  rawUnits.forEach((entry, index) => {
    const parsed = parseUnit(entry, index, issues);
    if (parsed) units.push(parsed);
  });
  const rawPetLimits = Array.isArray(payload.petLimits) ? payload.petLimits : [];
  const petLimits: BattlePreviewPetLimit[] = [];
  rawPetLimits.forEach((entry, index) => {
    const parsed = parsePetLimit(entry, index, issues);
    if (parsed) petLimits.push(parsed);
  });
  const diagnostics = Array.isArray(payload.diagnostics) ? payload.diagnostics.map(parseDiagnostic) : [];
  if (issues.length > 0) return { preview: null, issues };
  return { preview: { schema: BATTLE_PREVIEW_SCHEMA, units, petLimits, diagnostics }, issues: [] };
}

/** Pull the backend's own message out of an error body, matching the runner's error convention. */
export function describeBattlePreviewFailure(status: number, body: string): string {
  const text = (body ?? "").trim();
  if (!text) return `preview responded ${status} with an empty body`;
  try {
    const parsed: unknown = JSON.parse(text);
    if (isRecord(parsed) && typeof parsed.message === "string" && parsed.message.trim()) {
      return `${parsed.message.trim()} (preview HTTP ${status})`;
    }
  } catch {
    /* fall through to raw text */
  }
  return `${text.split(/\r?\n/).filter(Boolean).pop() ?? text} (preview HTTP ${status})`;
}

/**
 * Send the preview envelope. Aborts (a stale request being superseded) surface as
 * `BattlePreviewError` with `aborted: true` so the caller can ignore them.
 */
export async function requestBattlePreview(
  scenario: unknown,
  signal?: AbortSignal,
  fetchImpl: typeof fetch = fetch,
): Promise<BattlePreview> {
  const desktop = syntheticLegalDesktopApi();
  if (desktop) {
    let parsed: unknown;
    try {
      parsed = await desktop.preview_battle(buildBattlePreviewRequest(scenario));
    } catch (error) {
      throw new BattlePreviewError(`the desktop battle preview failed: ${error instanceof Error ? error.message : String(error)}`);
    }
    if (isRecord(parsed) && typeof parsed.message === "string" && parsed.message.trim() && parsed.schema !== BATTLE_PREVIEW_SCHEMA) {
      throw new BattlePreviewError(parsed.message.trim());
    }
    const { preview, issues } = parseBattlePreview(parsed);
    if (!preview) throw new BattlePreviewError(`the desktop preview response was rejected: ${issues.join("; ")}`);
    return preview;
  }
  let response: Response;
  try {
    response = await fetchImpl(BATTLE_RUN_ENDPOINT, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(buildBattlePreviewRequest(scenario)),
      signal,
    });
  } catch (error) {
    if (typeof (error as { name?: unknown })?.name === "string" && (error as { name: string }).name === "AbortError") {
      const aborted = new BattlePreviewError("preview request aborted");
      Object.defineProperty(aborted, "aborted", { value: true });
      throw aborted;
    }
    throw new BattlePreviewError(
      `the preview request failed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  const text = await response.text();
  if (!response.ok) throw new BattlePreviewError(describeBattlePreviewFailure(response.status, text));
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new BattlePreviewError(`the preview response is not JSON: ${String(error)}`);
  }
  if (isRecord(parsed) && typeof parsed.message === "string" && parsed.message.trim() && parsed.schema !== BATTLE_PREVIEW_SCHEMA) {
    throw new BattlePreviewError(parsed.message.trim());
  }
  const { preview, issues } = parseBattlePreview(parsed);
  if (!preview) throw new BattlePreviewError(`the preview response was rejected: ${issues.join("; ")}`);
  return preview;
}

export function isAbortedPreview(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && (error as { aborted?: unknown }).aborted === true);
}

