import {
  BATTLE_REPLAY_SCHEMA,
  parseBattleReplayResult,
  serializeBattleReplayResult,
  type BattleReplayResult,
  type ReplayVisualSetup,
} from "@/lib/battle-replay-result";

/**
 * Session store for one generated battle - PASS 16 COMMAND 16.9.
 *
 * The setup page stores the run result here and the replay page reads it, so no replay JSON ever
 * travels in the URL and no transient DOM object is relied on. The payload is versioned: an entry
 * written by an older store version, or one that no longer satisfies the replay contract, is
 * rejected instead of being shown as a half-valid battle.
 */
export const GENERATED_BATTLE_STORE_KEY = "ka-generated-battle-1";
export const GENERATED_BATTLE_STORE_VERSION = 1;

export type GeneratedBattleRecord = {
  storeVersion: number;
  /** the authoritative replay payload (never a reference fixture) */
  replay: BattleReplayResult;
  /**
   * The exact `ka-special-combat-research-1` scenario that produced `replay`, when the writer had it.
   *
   * Additive and optional: the replay payload alone does not carry the raw own-unit parameters, so an
   * interactive branch (see `@/lib/battle-interaction`) needs the source scenario. A record written
   * before this field existed stays readable; the interaction helper reports the scenario as
   * unavailable instead of reconstructing one from the replay.
   */
  scenarioJson?: string;
  /** renderer metadata the adapter produced for this exact setup */
  visualSetup?: ReplayVisualSetup;
  /** adapter warnings, kept separate from coverage notes */
  warnings: string[];
  /** what produced this battle, for the page header */
  summary: {
    encounterId: number;
    encounterTitle: string | null;
    defeatCount: number;
    mathSeed: number;
    libSeed: number;
    tickLimit: number;
    ownUnitCount: number;
    enemyUnitCount: number;
    storedAt: string;
  };
};

export type StoredBattleRead =
  | { status: "empty" }
  | { status: "ok"; record: GeneratedBattleRecord }
  | { status: "incompatible"; reason: string };

function storage(): Storage | null {
  try {
    if (typeof window === "undefined" || !window.sessionStorage) return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function createGeneratedBattleRecord(
  replay: BattleReplayResult,
  visualSetup: ReplayVisualSetup | undefined,
  warnings: string[],
  encounterTitle: string | null,
  scenarioJson?: string,
): GeneratedBattleRecord {
  const record: GeneratedBattleRecord = {
    storeVersion: GENERATED_BATTLE_STORE_VERSION,
    replay,
    ...(visualSetup ? { visualSetup } : {}),
    ...(scenarioJson ? { scenarioJson } : {}),
    warnings: [...warnings],
    summary: {
      encounterId: replay.setupSummary.encounterId,
      encounterTitle,
      defeatCount: replay.setupSummary.defeatCount,
      mathSeed: replay.setupSummary.mathSeed,
      libSeed: replay.setupSummary.libSeed,
      tickLimit: replay.setupSummary.tickLimit,
      ownUnitCount: replay.setupSummary.ownUnitCount,
      enemyUnitCount: replay.setupSummary.enemyUnitCount,
      storedAt: new Date().toISOString(),
    },
  };
  return record;
}

export function writeGeneratedBattle(
  replay: BattleReplayResult,
  visualSetup: ReplayVisualSetup | undefined,
  warnings: string[],
  encounterTitle: string | null,
  scenarioJson?: string,
): GeneratedBattleRecord {
  const record = createGeneratedBattleRecord(replay, visualSetup, warnings, encounterTitle, scenarioJson);
  storage()?.setItem(GENERATED_BATTLE_STORE_KEY, serializeBattleReplayRecord(record));
  return record;
}

/** Deterministic serialization: stable key order, compact, no DOM or window state. */
export function serializeBattleReplayRecord(record: GeneratedBattleRecord): string {
  const stable = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(stable);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value as Record<string, unknown>)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([key, entry]) => [key, stable(entry)]),
      );
    }
    return value;
  };
  return JSON.stringify(stable(record));
}

/**
 * Attach the source scenario to an existing record without touching the replay.
 *
 * Pure helper for a call site that stored a battle before it had the scenario, or that wants a record
 * for a branched scenario. `scenarioJson` is dropped when it is empty, so the field stays optional.
 */
export function withGeneratedBattleScenario(
  record: GeneratedBattleRecord,
  scenarioJson?: string,
): GeneratedBattleRecord {
  const { scenarioJson: _existing, ...rest } = record;
  return { ...rest, ...(scenarioJson ? { scenarioJson } : {}) };
}

export function readGeneratedBattle(raw?: string | null): StoredBattleRead {
  const text = raw ?? storage()?.getItem(GENERATED_BATTLE_STORE_KEY) ?? null;
  if (!text) return { status: "empty" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { status: "incompatible", reason: `stored battle is not JSON: ${String(error)}` };
  }
  if (!parsed || typeof parsed !== "object") {
    return { status: "incompatible", reason: "stored battle is not an object" };
  }
  const candidate = parsed as Partial<GeneratedBattleRecord>;
  if (candidate.storeVersion !== GENERATED_BATTLE_STORE_VERSION) {
    return {
      status: "incompatible",
      reason: `stored battle has store version ${String(candidate.storeVersion)}; this page writes ${GENERATED_BATTLE_STORE_VERSION}`,
    };
  }
  const { result, issues } = parseBattleReplayResult(candidate.replay);
  if (!result) {
    return { status: "incompatible", reason: `stored replay rejected: ${issues.join("; ")}` };
  }
  if (result.schema !== BATTLE_REPLAY_SCHEMA) {
    return { status: "incompatible", reason: `stored replay has schema ${result.schema}` };
  }
  return {
    status: "ok",
    record: {
      storeVersion: GENERATED_BATTLE_STORE_VERSION,
      replay: result,
      ...(candidate.visualSetup ? { visualSetup: candidate.visualSetup } : {}),
      ...(typeof candidate.scenarioJson === "string" ? { scenarioJson: candidate.scenarioJson } : {}),
      warnings: candidate.warnings ?? [],
      summary: candidate.summary ?? {
        encounterId: result.setupSummary.encounterId,
        encounterTitle: null,
        defeatCount: result.setupSummary.defeatCount,
        mathSeed: result.setupSummary.mathSeed,
        libSeed: result.setupSummary.libSeed,
        tickLimit: result.setupSummary.tickLimit,
        ownUnitCount: result.setupSummary.ownUnitCount,
        enemyUnitCount: result.setupSummary.enemyUnitCount,
        storedAt: "",
      },
    },
  };
}

export function clearGeneratedBattle(): void {
  storage()?.removeItem(GENERATED_BATTLE_STORE_KEY);
}

/** Deterministic fixture text for the stored battle (same key order as the replay serializer). */
export function serializeGeneratedBattle(record: GeneratedBattleRecord): string {
  return serializeBattleReplayResult({ ...record.replay, visualSetup: record.visualSetup });
}
