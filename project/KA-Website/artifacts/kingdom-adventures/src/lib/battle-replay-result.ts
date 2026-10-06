/**
 * Generic combat replay contract — PASS 16 COMMAND 16.4.
 *
 * This module is the TypeScript side of the Python exporter
 * `KA-Website/tools/recovery/combat_replay_export.py`: it validates, joins and folds one
 * `ka-battle-replay-1` payload. It contains no combat rules and no rendering geometry.
 *
 * Deliberately import-free so the browser harness and the Node driver can both load it.
 */

export const BATTLE_REPLAY_SCHEMA = "ka-battle-replay-1";

export type ReplaySide = "ally" | "enemy";

export type ReplayParameter = {
  raw: {
    rawValue: number;
    rawMax: number;
    extraValue: number;
    extraMax: number;
    trainingLevel: number;
  };
  effectiveValue: number | null;
  effectiveMaximum: number | null;
};

/** One fighter. `unitId` is the stable replay identity; `entityId` is the simulator's own id. */
export type BattleReplayUnit = {
  unitId: string;
  side: ReplaySide;
  rosterIndex: number;
  entityId: number;
  name: string;
  kind: "human" | "monster";
  human: boolean;
  monsterId: number | null;
  weaponId: number;
  skillIds: number[];
  invocationLevels: number[];
  grid: number;
  column: number;
  row: number;
  cell: [number, number];
  startHp: number;
  startMp: number;
  equipment?: Array<{ id: number; level: number; affinity: number }>;
  parameters: Record<string, ReplayParameter>;
  visitor?: boolean;
  leaderIdentity?: boolean;
  humanFlags?: number;
  isHouseOwner?: boolean;
  petOwnerName?: string;
  ownerPlayer?: boolean;
  boss?: boolean;
  level?: number;
  maxLevel?: number;
  rank?: number;
};

/** One normalized runner event. Every field is the runner's own, with ids re-labelled. */
export type BattleReplayEvent = {
  seq: number;
  tick: number;
  phase: string;
  kind: string;
  actorUnitId?: string | null;
  targetUnitId?: string | null;
  /** Array form of the same identity: one mapped unitId per element, order preserved. */
  targetUnitIds?: Array<string | null>;
  attackerUnitId?: string | null;
  casterUnitId?: string | null;
  ownerUnitId?: string | null;
  projectileId?: number;
  attackSeqs?: number[];
  /** null = the ordinary weapon attack; a number = the skill record that caused the event */
  skillId?: number | null;
  skillSlot?: number;
  stateName?: string;
  [key: string]: unknown;
};

export type BattleReplayFinalUnit = {
  unitId: string;
  entityId: number;
  side: ReplaySide;
  rosterIndex: number;
  hp: number;
  mp: number;
  state: number;
  stateName: string | null;
  commands: number;
  cell: [number, number];
};

/**
 * Additive report-only reward-entitlement block from the runner (`combat_reward_entitlement`).
 *
 * Optional for the same reason as `finish`: older payloads stop at finalState. It is a report, never
 * combat behaviour: `pendingChestCount` is the queued chest count, and `awardedChestCount` is 0 for a
 * native loss, otherwise the pending count certified at a frame STRICTLY BEFORE the verdict, or null
 * when no such certificate held. It is an entitlement count, not an inventory receipt.
 */
export type BattleReplayRewardEntitlement = {
  certificateId: string;
  certificate: {
    holds: boolean;
    frame: number | null;
    timingRule?: string;
    issuedBeforeVerdict?: boolean;
    clauses?: Record<string, boolean> | null;
    storedTargetHolders?: number[];
    bossTargetCommandHolders?: number[];
    bossHp?: number | null;
    bossState?: number | null;
    scope?: Record<string, unknown>;
    observations?: number;
    verdictObservations?: number;
    lateHold?: { frame: number; pendingChestCount: number; clauses?: Record<string, boolean> } | null;
  };
  battleVerdict: number | null;
  victoryRequired: boolean;
  victoryRequiredGate?: string;
  pendingChestCount: number;
  capturedPendingAtVerdict: number | null;
  awardedChestCount: number | null;
  awardedChestCountBasis: string;
  rewardCountSettled: boolean;
  rewardCountReason: string;
  lateCertificateObserved?: boolean;
  postCertificateQueueChanged: boolean;
  postCertificateQueueDelta: number;
  diagnosticFinish?: Record<string, unknown>;
  prizeCallbacks?: number;
  proofLimits?: string[];
};

export type BattleReplayCatalogSkill = {
  id: number;
  motion: number;
  type: number;
  value: number;
  minMp: number;
  maxMp: number;
  requiredEquipType: number;
  source: string;
};

export type BattleReplayCatalogEquipment = {
  id: number;
  name: string;
  type: number;
  motion: number;
  shootingRange: number;
  projectileFlag: boolean;
  source: string;
};

export type BattleReplayResult = {
  schema: string;
  source: { exporter: string; runner: string; scenarioSchema: string; note: string };
  setupSummary: {
    encounterId: number;
    defeatCount: number;
    mathSeed: number;
    libSeed: number;
    tickLimit: number;
    holyHerbStock: number;
    inputs: Array<{ tick: number; type: string; phase: string }>;
    /** Present once the scenario declares explicit recovery items; absent for legacy payloads. */
    items?: Record<string, { bonusCategory: number; bonusType: number; bonusMinValue: number; bonusMaxValue: number }>;
    itemStock?: Record<string, number>;
    /** Declared source conditions, carried through so a replay states its own simulation profile. */
    startProfile?: unknown;
    finishPolicy?: string;
    ownUnitCount: number;
    enemyUnitCount: number;
    prePlacement: string[];
    /** adapter warnings merged in by the TypeScript bridge; absent in the raw Python payload */
    warnings?: string[];
  };
  encounter: {
    encounterId: number;
    title: string | null;
    level: number;
    defeatCount: number;
    followerSelectionDraws: number;
    formationOrder: number[];
    ownFormationOrder: number[];
    enemyMonsterIds: number[];
  };
  units: BattleReplayUnit[];
  ticks: number;
  events: BattleReplayEvent[];
  finalState: {
    verdict: number | null;
    battleState: number;
    battleFrame: number;
    ticks: number;
    stopReason: string;
    censored: boolean;
    prizeCallbacks: number;
    mathDraws: number;
    libDraws: number;
    rngFinalState: unknown;
    retainedRewards: unknown;
    initializationMode: string;
    units: BattleReplayFinalUnit[];
    cellsDerivedFrom: string;
    /** Present once the runner reports it; absent for legacy payloads (fall back to prizeCallbacks). */
    rewardEntitlement?: BattleReplayRewardEntitlement;
    /* ---- Ending cut (additive; absent on legacy payloads) -------------------------------------
     * `ticks` is the engine horizon. The fields below separate the ENGINE's Verdict/Ending from the
     * declared-Finish tail so a player clamps its clock to `endingCutTick` instead of playing the
     * post-verdict silent tail (and the diagnostic horizon chest dispatch) as battle time.
     */
    verdictTick?: number | null;
    finishTick?: number | null;
    finishPolicy?: string;
    finishBoundary?: string;
    yieldTrusted?: boolean;
    rewardsTruncated?: boolean;
    endingConfirmed?: boolean | null;
    /** frame Ending was entered (the verdict tick): a whole team is in Leaving state 8 */
    endingTick?: number | null;
    /** last tick the engine simulated; the renderer clamps playback to this tick */
    endingCutTick?: number | null;
    /** the declared tick limit's last simulated tick (end of the silent tail) */
    horizonTick?: number | null;
    /** endingCutTick - endingTick: the kept knock-down/departure window after Ending */
    postEndingTicks?: number | null;
    /** ticks between endingCutTick and horizonTick that hold no authoritative simulation */
    silentTailTicks?: number | null;
    /** declared-Finish emissions (diagnostic chest dispatch + its RNG draws) excluded from the cut */
    finishPhaseEvents?: number | null;
    finishPhaseFirstTick?: number | null;
    endingCutReason?: string;
    /** A browser session has simulated through windowStopTick and can still advance. */
    windowed?: boolean;
    windowStopTick?: number | null;
    windowHorizonTick?: number | null;
    windowRemainingTicks?: number | null;
  };
  catalog: {
    stateNames: Record<string, string>;
    skills: Record<string, BattleReplayCatalogSkill>;
    equipment: Record<string, BattleReplayCatalogEquipment>;
  };
  metrics: Record<string, number>;
  holyHerbRemaining: number;
  holyHerbUses?: Array<{ item?: string; tick?: number; phase?: string; used?: boolean; remaining?: number }>;
  /** Engine item uses (one record per spent input) and the finite budget left after the run. */
  itemUses?: Array<{ item?: string; tick?: number; phase?: string; used?: boolean; remaining?: number }>;
  itemRemaining?: Record<string, number>;
  receipts: unknown;
  runnerLimits: string[];
  notes: string[];
  missing: string[];
  /** merged in by the TypeScript bridge for the future renderer layer */
  visualSetup?: ReplayVisualSetup;
  visuals?: Record<string, ReplayUnitVisual>;
};

/** Structural view of the adapter's visualSetup — duck-typed so this module stays import-free. */
export type ReplayVisualSetup = {
  encounter?: unknown;
  units: Array<{
    name: string;
    kind: "human" | "monster";
    jobId?: string;
    rank?: string;
    gender?: number;
    monsterId?: number;
    appearanceInputs?: { flag?: number; imgIds?: number[]; source: string };
    /** additive (BUILDER-CONTRACT.md): recovered equipment id of the shield slot, 0/null = none */
    shieldId?: number | null;
    weaponId: number;
    weaponMotion: number | null;
    skills: Array<{
      skillId: number;
      invocationLevel: 0 | 1 | 2;
      motion: number | null;
      requiredEquipType: number | null;
    }>;
  }>;
};

export type ReplayUnitVisual = {
  jobId?: string;
  rank?: string;
  gender?: number;
  appearanceInputs?: { flag?: number; imgIds?: number[]; source: string };
  /** additive (BUILDER-CONTRACT.md): recovered equipment id of the shield slot, 0/null = none */
  shieldId?: number | null;
  weaponId?: number;
  weaponMotion: number | null;
  skills: Array<{ skillId: number; invocationLevel: 0 | 1 | 2; motion: number | null }>;
};

/* ------------------------------------------------------------------ */
/* Parse / validate                                                     */
/* ------------------------------------------------------------------ */

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Structural check of a `ka-battle-replay-1` payload. It verifies the contract (schema, unique
 * identities, every final unit present, event references resolvable) and never re-runs combat.
 */
export function parseBattleReplayResult(value: unknown): { result?: BattleReplayResult; issues: string[] } {
  const issues: string[] = [];
  if (!isObject(value)) return { issues: ["replay payload is not an object"] };
  if (value.schema !== BATTLE_REPLAY_SCHEMA) {
    return { issues: [`unsupported replay schema ${String(value.schema)}`] };
  }
  for (const key of ["setupSummary", "encounter", "finalState", "catalog"]) {
    if (!isObject(value[key])) issues.push(`missing object ${key}`);
  }
  const units = value.units;
  if (!Array.isArray(units) || units.length === 0) issues.push("units must be a non-empty array");
  const events = value.events;
  if (!Array.isArray(events)) issues.push("events must be an array");
  if (issues.length) return { issues };

  const result = value as unknown as BattleReplayResult;
  const unitIds = new Set<string>();
  const entityIds = new Set<number>();
  for (const unit of result.units) {
    if (!unit.unitId) issues.push("unit without unitId");
    if (unitIds.has(unit.unitId)) issues.push(`duplicate unitId ${unit.unitId}`);
    if (entityIds.has(unit.entityId)) issues.push(`duplicate entityId ${unit.entityId}`);
    unitIds.add(unit.unitId);
    entityIds.add(unit.entityId);
    if (unit.side !== "ally" && unit.side !== "enemy") issues.push(`unit ${unit.unitId} has side ${unit.side}`);
    if (!Array.isArray(unit.skillIds) || !Array.isArray(unit.invocationLevels)) {
      issues.push(`unit ${unit.unitId} has no ordered skill lists`);
    } else if (unit.skillIds.length !== unit.invocationLevels.length) {
      issues.push(`unit ${unit.unitId} skill/slot length mismatch`);
    }
    if (typeof unit.startHp !== "number" || typeof unit.startMp !== "number") {
      issues.push(`unit ${unit.unitId} has no run-start HP/MP`);
    }
  }
  const finalUnits = result.finalState.units;
  const stateNames = result.catalog?.stateNames ?? {};
  const knownState = (id: unknown) => typeof id === "number" && typeof stateNames[String(id)] === "string";
  if (!Array.isArray(finalUnits) || finalUnits.length !== result.units.length) {
    issues.push("final state does not cover every unit");
  } else {
    for (const unit of finalUnits) {
      if (!unitIds.has(unit.unitId)) issues.push(`final state names unknown unit ${unit.unitId}`);
      if (typeof unit.hp !== "number" || typeof unit.mp !== "number" || typeof unit.state !== "number") {
        issues.push(`final state for ${unit.unitId} is incomplete`);
      }
      // States outside the recovered 1..8 BATTLE_STATE table (id 0 at battle teardown) stay unnamed.
      if (knownState(unit.state) && typeof unit.stateName !== "string") {
        issues.push(`final state for ${unit.unitId} has no recovered state name`);
      }
      if (!knownState(unit.state) && unit.stateName) {
        issues.push(`final state for ${unit.unitId} names state ${unit.state} outside the recovered table`);
      }
    }
  }
  const seqs = new Set<number>();
  for (const event of result.events as BattleReplayEvent[]) {
    if (typeof event.seq !== "number") issues.push("event without seq");
    else if (seqs.has(event.seq)) issues.push(`duplicate event seq ${event.seq}`);
    else seqs.add(event.seq);
    if (typeof event.tick !== "number" || typeof event.kind !== "string") {
      issues.push(`event ${event.seq} lacks tick/kind`);
    }
    for (const field of ["targetUnitId", "actorUnitId", "attackerUnitId", "casterUnitId", "ownerUnitId"] as const) {
      const id = event[field];
      if (typeof id === "string" && !unitIds.has(id)) issues.push(`event ${event.seq} names unknown unit ${id}`);
    }
    if (typeof event.skillId === "number" && !(String(event.skillId) in result.catalog.skills)) {
      issues.push(`event ${event.seq} names skill ${event.skillId} outside the catalog`);
    }
    if (event.kind === "state") {
      if (knownState(event.new) && typeof event.stateName !== "string") {
        issues.push(`state event ${event.seq} has no recovered state name`);
      }
      if (!knownState(event.new) && event.stateName) {
        issues.push(`state event ${event.seq} names state ${String(event.new)} outside the recovered table`);
      }
    }
  }
  return { result, issues };
}

/* ------------------------------------------------------------------ */
/* Deterministic serialization                                          */
/* ------------------------------------------------------------------ */

function stableSort(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableSort);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, entry]) => [key, stableSort(entry)]),
    );
  }
  return value;
}

/** Deterministic JSON for saving a replay fixture: stable key order, no volatile fields. */
export function serializeBattleReplayResult(result: BattleReplayResult): string {
  return JSON.stringify(stableSort(result), null, 2) + "\n";
}

/* ------------------------------------------------------------------ */
/* Joins, views and folds                                               */
/* ------------------------------------------------------------------ */

export function battleReplayUnitIndex(result: BattleReplayResult): {
  byUnitId: Map<string, BattleReplayUnit>;
  byEntityId: Map<number, BattleReplayUnit>;
} {
  return {
    byUnitId: new Map(result.units.map((unit) => [unit.unitId, unit])),
    byEntityId: new Map(result.units.map((unit) => [unit.entityId, unit])),
  };
}

/**
 * Attach the adapter's renderer metadata to the replay units, joined by unitId where possible and
 * by name for the enemy roster (which has no setup-side visual entry).
 *
 * The combat facts stay the simulator's; this only joins the recovered visual identity, exactly as
 * COMMAND 16.4 requires.
 */
export function attachReplayVisualSetup(
  result: BattleReplayResult,
  visualSetup: ReplayVisualSetup | undefined,
): BattleReplayResult {
  if (!visualSetup) return result;
  const visuals: Record<string, ReplayUnitVisual> = {};
  const allyUnits = result.units.filter((unit) => unit.side === "ally");
  visualSetup.units.forEach((visual, index) => {
    const byIndex = allyUnits[index];
    const byName = result.units.find((unit) => unit.name === visual.name);
    const unitId = byIndex?.name === visual.name ? byIndex.unitId : byName?.unitId;
    if (!unitId) return;
    visuals[unitId] = {
      ...(visual.jobId === undefined ? {} : { jobId: visual.jobId }),
      ...(visual.rank === undefined ? {} : { rank: visual.rank }),
      ...(visual.gender === undefined ? {} : { gender: visual.gender }),
      ...(visual.appearanceInputs === undefined ? {} : { appearanceInputs: visual.appearanceInputs }),
      ...(visual.shieldId === undefined ? {} : { shieldId: visual.shieldId }),
      weaponId: visual.weaponId,
      weaponMotion: visual.weaponMotion,
      skills: visual.skills.map((skill) => ({
        skillId: skill.skillId,
        invocationLevel: skill.invocationLevel,
        motion: skill.motion,
      })),
    };
  });
  return { ...result, visualSetup, visuals };
}

export type ReplayUnitTimelinePoint = {
  tick: number;
  seq: number;
  hp: number;
  mp: number;
  state: number;
  stateName: string | null;
  cell: [number, number];
};

export type ReplayTimeline = {
  ticks: number;
  /** true when the fold reached the run horizon: the final point is the runner's final state */
  complete: boolean;
  derived: string;
  units: Record<string, ReplayUnitTimelinePoint[]>;
};

/**
 * Fold the event stream into per-unit state over time. This is a *derived* view: the events remain
 * the authoritative record, and the last point of every unit is the runner's own final state.
 */
export function foldBattleReplayTimeline(result: BattleReplayResult): ReplayTimeline {
  const state: Record<string, ReplayUnitTimelinePoint[]> = {};
  const current: Record<string, ReplayUnitTimelinePoint> = {};
  for (const unit of result.units) {
    const point: ReplayUnitTimelinePoint = {
      tick: 0,
      seq: -1,
      hp: unit.startHp,
      mp: unit.startMp,
      state: 0,
      stateName: null,
      cell: [unit.cell[0], unit.cell[1]],
    };
    current[unit.unitId] = point;
    state[unit.unitId] = [point];
  }
  const push = (unitId: string | null | undefined, tick: number, seq: number, event: BattleReplayEvent) => {
    if (!unitId) return;
    const previous = current[unitId];
    if (!previous) return;
    const fields = event as Record<string, unknown>;
    const after = typeof fields.after === "number" ? (fields.after as number) : null;
    const parameter = typeof fields.parameter === "number" ? (fields.parameter as number) : null;
    /* `resource_change` is the item path's authoritative per-parameter delta: consume the runner's own
       `after` for HP(10)/MP(11) verbatim. Nothing is recomputed from a healing formula. */
    const resource = event.kind === "resource_change";
    const point: ReplayUnitTimelinePoint = {
      tick,
      seq,
      hp: typeof event.hpAfter === "number"
        ? (event.hpAfter as number)
        : after !== null && (event.kind === "heal" || (resource && parameter === 10))
          ? after
          : previous.hp,
      mp: after !== null && (event.kind === "mp" || (resource && parameter === 11)) ? after : previous.mp,
      state: event.kind === "state" && typeof event.new === "number" ? (event.new as number) : previous.state,
      stateName: event.kind === "state" && typeof event.stateName === "string" ? event.stateName : previous.stateName,
      cell: Array.isArray(event.cell) && event.kind === "cell_change"
        ? [(event.cell as number[])[0], (event.cell as number[])[1]]
        : [previous.cell[0], previous.cell[1]],
    };
    current[unitId] = point;
    state[unitId].push(point);
  };
  for (const event of result.events as BattleReplayEvent[]) {
    if (event.kind === "attack") push(event.targetUnitId, event.tick, event.seq, event);
    else if (event.kind === "heal") push(event.targetUnitId, event.tick, event.seq, event);
    else if (event.kind === "mp") push(event.casterUnitId, event.tick, event.seq, event);
    else if (event.kind === "state") push(event.targetUnitId, event.tick, event.seq, event);
    else if (event.kind === "resource_change") push(event.targetUnitId, event.tick, event.seq, event);
    else if (event.kind === "cell_change") push(event.targetUnitId, event.tick, event.seq, event);
  }
  for (const unit of result.finalState.units) {
    const points = state[unit.unitId];
    if (!points) continue;
    points.push({
      tick: result.ticks,
      seq: Number.MAX_SAFE_INTEGER,
      hp: unit.hp,
      mp: unit.mp,
      state: unit.state,
      stateName: unit.stateName,
      cell: [unit.cell[0], unit.cell[1]],
    });
  }
  return {
    ticks: result.ticks,
    complete: true,
    derived: "event fold over attack/heal/mp/resource_change/state/cell_change, closed by the runner's final state",
    units: state,
  };
}

/**
 * The tick a replay should play to.
 *
 * Uses the runner's ending cut (the last tick the engine simulated after Ending), so the losing side's
 * knock-down/departure window still plays while the post-verdict silent tail - and a declared-Finish
 * chest dispatch parked at the horizon - are not treated as battle time. Falls back to the last event
 * tick, then to the horizon, for legacy payloads that predate the cut. No combat state is derived.
 */
export function battlePlaybackEndTick(result: BattleReplayResult): number {
  const state = result.finalState;
  if (typeof state.endingCutTick === "number") return state.endingCutTick;
  // Legacy payload: exported before `endingCutTick` existed (a stored replay, or a response from a
  // runtime package that predates the ending cut). Fold the SAME rule the exporter uses from the
  // payload's own event phases instead of playing the declared horizon: the last tick with an event
  // outside the declared `finish` phase. That keeps the knock-down/revive/departure window and drops
  // the post-verdict silent tail plus the diagnostic horizon chest dispatch. Nothing is invented -
  // the boundary comes from the events this payload already carries.
  let simulated = -1;
  let lastEvent = -1;
  for (const event of result.events as BattleReplayEvent[]) {
    if (typeof event.tick !== "number") continue;
    if (event.tick > lastEvent) lastEvent = event.tick;
    if (event.phase !== "finish" && event.tick > simulated) simulated = event.tick;
  }
  if (simulated >= 0) return simulated;
  if (typeof state.verdictTick === "number") return state.verdictTick;
  return lastEvent >= 0 ? lastEvent : Math.max(0, state.ticks - 1);
}

export function battleReplaySummary(result: BattleReplayResult): {
  encounterId: number;
  unitCount: number;
  allyCount: number;
  enemyCount: number;
  eventCount: number;
  ticks: number;
  verdict: number | null;
  koCount: number;
  leavingCount: number;
  skillEventCount: number;
  weaponAttackCount: number;
  finalHpTotal: number;
} {
  const events = result.events as BattleReplayEvent[];
  return {
    encounterId: result.setupSummary.encounterId,
    unitCount: result.units.length,
    allyCount: result.units.filter((unit) => unit.side === "ally").length,
    enemyCount: result.units.filter((unit) => unit.side === "enemy").length,
    eventCount: events.length,
    ticks: result.ticks,
    verdict: result.finalState.verdict,
    koCount: events.filter((event) => event.kind === "attack" && event.hpAfter === 0).length,
    leavingCount: events.filter((event) => event.kind === "state" && event.new === 8).length,
    skillEventCount: events.filter((event) => typeof event.skillId === "number").length,
    weaponAttackCount: events.filter((event) => event.kind === "attack" && event.skillId === null).length,
    finalHpTotal: result.finalState.units.reduce((total, unit) => total + unit.hp, 0),
  };
}
