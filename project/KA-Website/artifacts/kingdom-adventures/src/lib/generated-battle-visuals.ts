/**
 * Generated-replay VISUAL adapter - product/visual pass, 2026-09-20.
 *
 * The one place that turns an authoritative `ka-battle-replay-1` timeline into what a player sees on
 * the shared battlefield stage. It is presentation-only: every value is read from the runner's own
 * events or from the canonical catalogs, nothing is re-simulated and no game fact is invented.
 *
 * Reused recovered sources (no second copy of any of them):
 *  * damage-number styles and the critical row - `NUMBER_ROW_Y` / `NUMBER_ROW_IMAGES`
 *    (battle-replay.ts, CONFIRMED CreateEffect args);
 *  * status words - `COMBAT_TEXT_ROWS` (attack_str, CONFIRMED);
 *  * skill identity - `SKILL_BY_ID` display name plus the canonical skill-icon map;
 *  * treasure sprite - `TREASURE_BY_ID[treasureId].icon` from the treasure catalog;
 *  * consumable identity - `CANONICAL_RECOVERY_ITEMS` plus the canonical item-icon map;
 *  * treasure-box icons for the chest queue, and item icons for the consumable bar.
 *
 * Two honesty rules this module encodes:
 *  1. a chest drop is PROVISIONAL until the runner's own reward-entitlement report certifies it,
 *     and a certified count is an entitlement, never a collected inventory;
 *  2. a skill balloon is drawn only for a runner `release` event with `used === true` - the
 *     recovered `use_fighter_skill` path that pays MP and calls CreateSkillBalloon. A failed
 *     invocation check (`invocation.passed === false`) is never drawn as a skill.
 */
import type { CombatTextLabel, NumberStyle } from "@/lib/battle-replay";
import type { BattleReplayEvent, BattleReplayResult } from "@/lib/battle-replay-result";
import { EQUIPMENT_BY_ID, SKILL_BY_ID, canonicalRecoveryItem } from "@/lib/battle-setup";
import {
  HOLY_HERB_ITEM,
  interactionCapabilities,
  stockAtTick,
} from "@/lib/battle-interaction";
import { renderSkillName } from "@/lib/battle-legality";
import { getItemIcon } from "@/lib/equipment-icons";
import {
  generatedHumanCharacter,
  type GeneratedFinishOutcome,
  type GeneratedHumanRules,
  type GeneratedUnitView,
} from "@/lib/generated-battle-view";
import { getSkillIcon } from "@/lib/skill-icons";
import { PARAMETER_NATIVE_NAMES } from "@/game-data/stat-parameter-ids";
import { TREASURE_BY_ID } from "@/lib/treasure-lookup";

/** A point on the shared 481x197 scene; the same string form `generatedScenePlacement` returns. */
export type GeneratedStageAnchor = { left: string; top: string };

export type GeneratedStageOverlay =
  | { key: string; kind: "arrow"; from: GeneratedStageAnchor; to: GeneratedStageAnchor; flipped: boolean }
  | {
      key: string;
      kind: "impact";
      anchor: GeneratedStageAnchor;
      scale: number;
      /** the impact's own animation frame, 0..GENERATED_IMPACT_LIFETIME_TICKS-1 */
      frame: number;
    }
  | { key: string; kind: "combat-text"; anchor: GeneratedStageAnchor; label: CombatTextLabel }
  | { key: string; kind: "damage"; anchor: GeneratedStageAnchor; amount: number; style: NumberStyle }
  | { key: string; kind: "heal"; anchor: GeneratedStageAnchor; amount: number }
  | {
      key: string;
      kind: "skill";
      anchor: GeneratedStageAnchor;
      /** the caster's own side, so the balloon can use the recovered ally/enemy frame */
      side: "ally" | "enemy" | null;
      skillId: number;
      name: string | null;
      icon: string | null;
      /** the releasing unit, so the balloon can resolve its declared invocation level */
      casterUnitId: string | null;
      invocationLevel: number | null;
      slot: number | null;
    };

/** The recovered display name and canonical icon for one skill id, or null when unmapped. */
export function generatedSkillIdentity(
  skillId: number,
): { name: string; icon: string | null; motion: number | null } | null {
  const entry = SKILL_BY_ID.get(skillId);
  if (!entry) return null;
  const name = renderSkillName(entry);
  return { name, icon: getSkillIcon(name) ?? null, motion: entry.motion ?? null };
}

function numberField(event: BattleReplayEvent, key: string): number | null {
  const value = (event as Record<string, unknown>)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function boolField(event: BattleReplayEvent, key: string): boolean | null {
  const value = (event as Record<string, unknown>)[key];
  return typeof value === "boolean" ? value : null;
}

function textField(event: BattleReplayEvent, key: string): string | null {
  const value = (event as Record<string, unknown>)[key];
  return typeof value === "string" ? value : null;
}

/**
 * The recovered impact-effect scale (`NATIVE_EFFECT_SPECS[0]`, CONFIRMED CreateEffect arguments):
 * the normal-hit impact is drawn at the effect's own size (100) and only a critical hit scales it to
 * 150, i.e. 1x and 1.5x. The previous 1.8x/2.4x pair was not recovered from that note and made every
 * hit burst read oversized next to the 24x30 fighter art.
 */
export function generatedImpactScale(critical: boolean): number {
  return critical ? 1.5 : 1;
}

/** The recovered vertical offset of the impact effect (`NATIVE_EFFECT_SPECS[0].offsetY = 10`). */
export const GENERATED_IMPACT_OFFSET_Y = 10;

/**
 * CONFIRMED impact geometry, read from the canonical recovered native layer (no new RE):
 *
 *  * `effect/effect_00.seb` (the intact original under
 *    `RE-evidence/20260911-building/placement/effect-original/`) is ONE layer with `maxFrame` 10:
 *    ten records that all name the same 60x60 cell at `transX/transY = -30/-30`, with frames 0,1 at
 *    u=0, 2,3 at u=60, 4,5 at u=120, 6,7 at u=180 and 8,9 at u=240.
 *  * `effect_00.opt` parsed with the recovered `parseOptSprite` (`src/lib/opt-sprite.ts`) says that
 *    60x60 cell is a 5x1 grid whose cells are one component each: the burst grows 12x11 -> 18x17 ->
 *    28x27 -> 32x31 -> 36x35 and every crop carries its own cell offset.
 *
 * So the shipped `impact-effect_00.png` (114x35) is the optimizer's pack of all five frames, not one
 * sprite: drawing the file whole paints five bursts at once, four of them at the wrong size - the
 * "repeated large yellow crosses" in the screenshots. One drawn impact must be the single cell crop.
 */
export const GENERATED_IMPACT_CELLS = [
  { sourceX: 96, sourceY: 17, width: 12, height: 11, cellX: 24, cellY: 27 },
  { sourceX: 96, sourceY: 0, width: 18, height: 17, cellX: 21, cellY: 24 },
  { sourceX: 68, sourceY: 0, width: 28, height: 27, cellX: 16, cellY: 19 },
  { sourceX: 36, sourceY: 0, width: 32, height: 31, cellX: 14, cellY: 16 },
  { sourceX: 0, sourceY: 0, width: 36, height: 35, cellX: 12, cellY: 13 },
] as const;

/** `effect_00.seb` draws every record at this translation, relative to the target's own origin. */
export const GENERATED_IMPACT_TRANS_X = -30;
export const GENERATED_IMPACT_TRANS_Y = -30;

/** SEB frames per OPT cell: frames 0,1 -> cell 0, 2,3 -> cell 1, ... 8,9 -> cell 4. */
export const GENERATED_IMPACT_FRAMES_PER_CELL = 2;

/** `effect_00.seb` `maxFrame`: the burst plays ten frames (one per tick) and then stops. */
export const GENERATED_IMPACT_LIFETIME_TICKS = 10;

/**
 * The one crop to draw for an impact animation frame, plus where its centre lands on the target's own
 * scene anchor. The recovered SEB translation puts the 60x60 cell on the target and the OPT cell
 * offset then places the crop inside it, so the centre is `trans + cell offset + half the crop`.
 */
export function generatedImpactFrame(frame: number): {
  sourceX: number;
  sourceY: number;
  width: number;
  height: number;
  centerX: number;
  centerY: number;
} {
  const index = Math.max(
    0,
    Math.min(GENERATED_IMPACT_CELLS.length - 1, Math.floor(frame / GENERATED_IMPACT_FRAMES_PER_CELL)),
  );
  const cell = GENERATED_IMPACT_CELLS[index];
  return {
    sourceX: cell.sourceX,
    sourceY: cell.sourceY,
    width: cell.width,
    height: cell.height,
    centerX: GENERATED_IMPACT_TRANS_X + cell.cellX + cell.width / 2,
    centerY: GENERATED_IMPACT_TRANS_Y + cell.cellY + cell.height / 2,
  };
}

/** The recovered damage-number style: critical row wins, otherwise the attacker's own side. */
export function generatedNumberStyle(critical: boolean, attackerSide: "ally" | "enemy" | null): NumberStyle {
  if (critical) return "critical";
  return attackerSide === "enemy" ? "enemy" : "ally";
}

/**
 * The visual layer of ONE replay frame. Events are consumed in the order the payload lists them
 * (seq order) and each produces at most one readable effect:
 *
 *  * `attack` (the runner's real hit record, not the `attack_batch` bookkeeping) ->
 *    attacker-to-target arrow, target impact, damage number, "Missed" when `hit === false`, and the
 *    recovered critical text on a critical hit;
 *  * `heal` -> the target's healing number;
 *  * `release` with `used === true` -> the skill balloon for that caster and skill (icon + name).
 */
export function buildGeneratedStageOverlays(input: {
  events: BattleReplayEvent[];
  anchorOf: (unitId: string | null | undefined) => GeneratedStageAnchor | null;
  sideOf: (unitId: string | null | undefined) => "ally" | "enemy" | null;
  /**
   * Impact-only window: the displayed tick's events plus the preceding ticks' events, with
   * `impactTick` the displayed tick. A landed hit keeps drawing one growing burst for the recovered
   * `effect_00.seb` lifetime instead of a single tick. Omitted, only `events` are considered and
   * every hit draws at frame 0 - the single-tick behaviour of a caller that has no window.
   */
  impactEvents?: BattleReplayEvent[];
  impactTick?: number | null;
}): GeneratedStageOverlay[] {
  const { events, anchorOf, sideOf } = input;
  const impactEvents = input.impactEvents ?? events;
  const impactTick = input.impactTick ?? null;
  const overlays: GeneratedStageOverlay[] = [];
  for (const event of events) {
    const kind = event.kind.toLowerCase();
    const attackerId = event.attackerUnitId ?? event.casterUnitId ?? event.actorUnitId ?? null;
    const targetId = event.targetUnitId ?? null;
    if (kind === "attack") {
      const target = anchorOf(targetId);
      if (!target) continue;
      const attacker = anchorOf(attackerId);
      const attackerSide = sideOf(attackerId);
      if (attacker) {
        overlays.push({
          key: `arrow-${event.seq}`,
          kind: "arrow",
          from: attacker,
          to: target,
          flipped: attackerSide === "enemy",
        });
      }
      const critical = boolField(event, "critical") === true;
      if (boolField(event, "hit") === false) {
        overlays.push({ key: `text-${event.seq}`, kind: "combat-text", anchor: target, label: "Missed" });
        continue;
      }
      const damage = numberField(event, "damage");
      if (damage !== null) {
        overlays.push({
          key: `damage-${event.seq}`,
          kind: "damage",
          anchor: target,
          amount: damage,
          style: generatedNumberStyle(critical, attackerSide),
        });
      }
      if (critical) {
        overlays.push({ key: `crit-${event.seq}`, kind: "combat-text", anchor: target, label: "Critical Hit" });
      }
      continue;
    }
    if (kind === "heal") {
      const target = anchorOf(targetId);
      const amount = numberField(event, "amount");
      if (target && amount !== null) {
        overlays.push({ key: `heal-${event.seq}`, kind: "heal", anchor: target, amount });
      }
      continue;
    }
    if (kind === "release") {
      const used = boolField(event, "used") === true;
      const skillId = numberField(event, "skillId");
      const caster = anchorOf(attackerId);
      if (!used || skillId === null || !caster) continue;
      const identity = generatedSkillIdentity(skillId);
      overlays.push({
        key: `skill-${event.seq}`,
        kind: "skill",
        anchor: caster,
        side: sideOf(attackerId),
        skillId,
        name: identity?.name ?? null,
        icon: identity?.icon ?? null,
        casterUnitId: attackerId,
        invocationLevel: numberField(event, "level"),
        slot: numberField(event, "skillSlot"),
      });
    }
  }
  /*
   * Impacts are collected after every other overlay: they are the only kind with a lifetime longer
   * than the tick they belong to, so they are the only ones read from the window rather than from
   * this tick's own events.
   */
  for (const event of impactEvents) {
    if (event.kind.toLowerCase() !== "attack") continue;
    if (boolField(event, "hit") === false) continue;
    const target = anchorOf(event.targetUnitId ?? null);
    if (!target) continue;
    const elapsed = impactTick === null ? 0 : impactTick - event.tick;
    if (elapsed < 0 || elapsed >= GENERATED_IMPACT_LIFETIME_TICKS) continue;
    overlays.push({
      key: `impact-${event.seq}`,
      kind: "impact",
      anchor: target,
      scale: generatedImpactScale(boolField(event, "critical") === true),
      frame: elapsed,
    });
  }
  return overlays;
}

/**
 * Every skill the runner actually released (`release.used === true`), in timeline order. The
 * invocation RNG check (`invocation.passed`) is reported separately because a failed check is not a
 * cast: the UI must not show a skill that never went off.
 */
export type GeneratedSkillRelease = {
  seq: number;
  tick: number;
  casterUnitId: string | null;
  skillId: number;
  name: string | null;
  icon: string | null;
  slot: number | null;
  invocationLevel: number | null;
};

export function generatedSkillReleases(replay: BattleReplayResult): GeneratedSkillRelease[] {
  const releases: GeneratedSkillRelease[] = [];
  for (const event of replay.events as BattleReplayEvent[]) {
    if (event.kind.toLowerCase() !== "release") continue;
    if (boolField(event, "used") !== true) continue;
    const skillId = numberField(event, "skillId");
    if (skillId === null) continue;
    const identity = generatedSkillIdentity(skillId);
    releases.push({
      seq: event.seq,
      tick: event.tick,
      casterUnitId: (event.casterUnitId ?? event.attackerUnitId ?? event.actorUnitId ?? null) as string | null,
      skillId,
      name: identity?.name ?? null,
      icon: identity?.icon ?? null,
      slot: numberField(event, "skillSlot"),
      invocationLevel: numberField(event, "level"),
    });
  }
  return releases.sort((a, b) => a.seq - b.seq);
}

/**
 * The declared invocation level behind one release: `0` High, `1` Normal (native default), `2` Low
 * (`GetInvocationRate` index; BUILDER-CONTRACT.md section 3).
 *
 * The runner's `release` event does not carry the level, so it is joined from the replay unit's own
 * declared slots (`skillIds` / `invocationLevels`, the arrays the scenario declared and the adapter
 * already forwards). A payload that does carry a level wins over the join. When the reported slot
 * does not name that skill, or no slot was reported and the skill is declared in more than one slot,
 * the level stays null - a wrong level is worse than none. It is the declared trigger rate, not the
 * outcome of this cast; that is the runner's own `invocation` event.
 */
export function generatedReleaseInvocation(
  release: { invocationLevel: number | null; casterUnitId: string | null; skillId: number; slot: number | null },
  units: ReadonlyArray<{ unitId: string; skillIds?: number[]; invocationLevels?: number[] }>,
): 0 | 1 | 2 | null {
  const reported = release.invocationLevel;
  if (reported === 0 || reported === 1 || reported === 2) return reported;
  const unit = units.find((candidate) => candidate.unitId === release.casterUnitId);
  const ids = unit?.skillIds;
  const levels = unit?.invocationLevels;
  if (!ids || !levels) return null;
  const levelAt = (index: number): 0 | 1 | 2 | null => {
    if (ids[index] !== release.skillId) return null;
    const level = levels[index];
    return level === 0 || level === 1 || level === 2 ? level : null;
  };
  if (release.slot !== null) return levelAt(release.slot);
  const matches = ids.map((_, index) => index).filter((index) => ids[index] === release.skillId);
  return matches.length === 1 ? levelAt(matches[0]) : null;
}

/* ------------------------------------------------------------------ */
/* Chests: the runner's own prize callbacks                            */
/* ------------------------------------------------------------------ */

/**
 * One queued chest. The runner emits `prize` at the leaving tick of the rival leader
 * (`combat_shared_controllers.leave` -> `enter_leaving_special`), with the treasure row it selected
 * from the encounter's reward group. `treasureId` is null only for a payload whose run had no
 * candidate rows; such a drop is shown without a sprite instead of a guessed one.
 */
export type GeneratedChestDrop = {
  seq: number;
  tick: number;
  treasureId: number | null;
  /** canonical treasure name when the id resolves in the treasure catalog */
  name: string | null;
  /** canonical treasure sprite path when the id resolves */
  icon: string | null;
  /** the unit whose departure queued the chest (the leaving rival leader) */
  targetUnitId: string | null;
};

export function generatedChestDrops(replay: BattleReplayResult): GeneratedChestDrop[] {
  const drops: GeneratedChestDrop[] = [];
  for (const event of replay.events as BattleReplayEvent[]) {
    if (event.kind.toLowerCase() !== "prize") continue;
    const treasureId = numberField(event, "treasureId");
    const treasure = treasureId === null ? undefined : TREASURE_BY_ID.get(treasureId);
    drops.push({
      seq: event.seq,
      tick: event.tick,
      treasureId,
      name: treasure?.name ?? null,
      icon: treasure?.icon ?? null,
      targetUnitId: (event.targetUnitId ?? null) as string | null,
    });
  }
  return drops.sort((a, b) => a.seq - b.seq);
}

/** Chests queued by the runner at or before `tick` - the live counter while the fight plays. */
export function generatedChestsAt(drops: GeneratedChestDrop[], tick: number): number {
  let count = 0;
  for (const drop of drops) {
    if (drop.tick <= tick) count += 1;
  }
  return count;
}

export type GeneratedChestSummary = {
  /** chests the runner queued in this replay (provisional until the entitlement report) */
  queued: number;
  /** the entitlement count certified before the verdict, or null when unknown */
  awarded: number | null;
  /** the runner's pending queue size at the finish boundary */
  pending: number;
  victoryRequired: boolean | null;
  /** writable, non-technical explanation of the difference between the two counts */
  note: string;
  drops: GeneratedChestDrop[];
};

/**
 * Player-facing chest summary. The queued count is what the player watched pop during the fight;
 * the awarded count is the runner's own certified entitlement (`awardedChestCount`), which the
 * completed-chest fixture does not prove is a town inventory receipt. Neither is called earned
 * inventory here.
 */
export function generatedChestSummary(
  outcome: GeneratedFinishOutcome,
  drops: GeneratedChestDrop[],
): GeneratedChestSummary {
  const entitlement = outcome.rewardEntitlement;
  const queued = drops.length;
  if (!entitlement) {
    return {
      queued,
      awarded: null,
      pending: outcome.prizeCallbacks,
      victoryRequired: null,
      note:
        "This replay predates the reward-entitlement report, so only the queued chest count is known. " +
        "A queued chest is not a collected one.",
      drops,
    };
  }
  // Some native reports omit eligibility fields even though older TypeScript-facing adapters
  // described them as nullable. Treat absent or malformed counts as unknown, never as a UI number.
  const awarded = typeof entitlement.awardedChestCount === "number"
    && Number.isSafeInteger(entitlement.awardedChestCount)
    && entitlement.awardedChestCount >= 0
    ? entitlement.awardedChestCount
    : null;
  let note: string;
  if (awarded !== null) {
    note =
      awarded === queued
        ? "The simulation marks every queued chest eligible. Actual reward settlement and collection are not yet validated."
        : `The simulation marks ${awarded} of ${queued} queued chests eligible. Actual reward settlement and collection are not yet validated.`;
  } else if (entitlement.battleVerdict === null) {
    note = "The battle did not resolve, so the queued chest count remains provisional.";
  } else {
    note =
      "The queue was never certified unchanged before the verdict, so the credited count is unknown; " +
      "the queued count stays provisional.";
  }
  return {
    queued,
    awarded,
    pending: entitlement.pendingChestCount,
    victoryRequired: entitlement.victoryRequired,
    note,
    drops,
  };
}

/**
 * The frame index showing `tick`, clamped into the frame list: an exact tick, else the newest frame at
 * or before it, else the first frame. `-1` only for an empty list.
 *
 * After a branch the player keeps watching the same moment: the branch preserves every event before the
 * clicked tick, so the clicked tick exists in the new timeline and the clock is re-anchored there
 * instead of jumping back to 0.
 */
export function generatedFrameIndexForTick(
  frames: ReadonlyArray<{ tick: number }>,
  tick: number,
): number {
  if (frames.length === 0) return -1;
  const exact = frames.findIndex((frame) => frame.tick === tick);
  if (exact >= 0) return exact;
  let before = -1;
  for (let index = 0; index < frames.length; index += 1) {
    if (frames[index].tick <= tick) before = index;
    else break;
  }
  return before >= 0 ? before : 0;
}

/* ------------------------------------------------------------------ */
/* Consumables: stock, availability and the known usable condition      */
/* ------------------------------------------------------------------ */

export type GeneratedConsumableRow = {
  /** the scenario key: "holy_herb" or the declared item name */
  key: string;
  label: string;
  icon: string | null;
  parameter: number | null;
  parameterName: string | null;
  scope: "all" | "single" | null;
  /** supported = the recovered battle touch path can reach this row's branch */
  supported: boolean;
  disabledReason: string | null;
  declaredStock: number;
  /** remaining stock at the displayed tick (the runner's own `battle_item.remaining`) */
  stockAtTick: number;
  /** a known usable condition holds at this tick - it is NOT a claim about optimal timing */
  usableAtTick: boolean;
  usableReason: string;
  /**
   * The recovered technical explanation that belongs to a row the battle touch path cannot reach
   * (native symbol / address prose). It is never the player-facing line; the bar shows it collapsed.
   */
  technicalReason: string | null;
};

/** The one player-facing word for a row the recovered battle touch path cannot reach. */
export const CONSUMABLE_UNSUPPORTED_TEXT = "Not usable in battle";

/**
 * The usable condition recovered for a recovery row: the item must be supported, have stock at the
 * displayed tick, and have at least one own-team fighter that (a) is not in the leaving /
 * knocked-down states the native caller filters out (0x14f34ac excludes 7/8) and (b) is below the
 * row's parameter maximum - the recovered `CanRecoveryResidentParameter` test (`rate < 100`).
 *
 * This is deliberately a condition, not a recommendation: the UI labels the row "usable now", never
 * "best time".
 */
function recoveryUsableCondition(
  row: { supported: boolean; parameter: number | null; stock: number },
  frameUnits: Record<string, { hp: number; mp: number; maxHp: number | null; maxMp: number | null; state: number }> | null,
): { usable: boolean; reason: string } {
  if (!row.supported) return { usable: false, reason: CONSUMABLE_UNSUPPORTED_TEXT };
  if (row.stock <= 0) return { usable: false, reason: "no stock left at this tick" };
  if (!frameUnits) return { usable: false, reason: "no fight state is displayed" };
  const eligible = Object.entries(frameUnits).filter(
    ([unitId, unit]) => unitId.startsWith("ally:") && unit.state !== 7 && unit.state !== 8,
  );
  if (eligible.length === 0) return { usable: false, reason: "no own-team fighter can receive it" };
  const parameter = row.parameter;
  if (parameter === 10 || parameter === 11) {
    const maximumKey = parameter === 10 ? "maxHp" : "maxMp";
    if (eligible.some(([, unit]) => unit[maximumKey] === null)) {
      return { usable: false, reason: "native parameter maximum unavailable in this replay" };
    }
    const below = eligible.some(([, unit]) => parameter === 10
      ? unit.hp < (unit.maxHp as number) : unit.mp < (unit.maxMp as number));
    const name = PARAMETER_NATIVE_NAMES[parameter] ?? `parameter ${parameter}`;
    if (!below) return { usable: false, reason: `every fighter is already at full ${name}` };
    return { usable: true, reason: `a fighter is below full ${name}` };
  }
  /* a parameter this view keeps no live track for: only the stock condition is claimed */
  return { usable: true, reason: "stock available" };
}

/**
 * The consumable bar rows for the displayed replay and tick.
 *
 * Support, scope and stock come from the interaction contract's own helpers
 * (`interactionCapabilities` + `stockAtTick` in `lib/battle-interaction.ts`), so the bar can never
 * disagree with the branch endpoint about what is usable or how much stock is left; this module only
 * adds the readable identity (canonical icon/name) and the recovered usable condition above.
 */
export function generatedConsumableRows(input: {
  replay: BattleReplayResult;
  tick: number;
  /** the stored source scenario; without it the rows are shown but cannot be branched */
  scenario?: unknown;
  frameUnits?: Record<string, { hp: number; mp: number; maxHp: number | null; maxMp: number | null; state: number }> | null;
}): GeneratedConsumableRow[] {
  const { replay, tick, scenario, frameUnits } = input;
  return interactionCapabilities({ scenario, replay }).map((capability) => {
    const canonical = canonicalRecoveryItem(capability.name === HOLY_HERB_ITEM ? "Holy Herb" : capability.name);
    const label = canonical?.name ?? (capability.name === HOLY_HERB_ITEM ? "Holy Herb" : capability.name);
    const parameter = capability.parameter ?? canonical?.parameter ?? null;
    const stock = stockAtTick(replay, capability.name, tick);
    const supported = capability.supported && capability.disabledReason === null;
    const ended = typeof replay.finalState.verdictTick === "number" && tick >= replay.finalState.verdictTick;
    const usable = ended
      ? { usable: false, reason: "The battle has ended." }
      : recoveryUsableCondition({ supported, parameter, stock }, frameUnits ?? null);
    return {
      key: capability.name,
      label,
      icon: getItemIcon(label) ?? null,
      parameter,
      parameterName: parameter === null ? null : PARAMETER_NATIVE_NAMES[parameter] ?? null,
      scope: capability.scope,
      supported,
      disabledReason: capability.disabledReason,
      declaredStock: capability.declaredStock,
      stockAtTick: stock,
      usableAtTick: usable.usable,
      usableReason: usable.reason,
      technicalReason: capability.disabledReason ?? null,
    };
  });
}

/* ------------------------------------------------------------------ */
/* Equipped character sprites via the shared loadout renderer           */
/* ------------------------------------------------------------------ */

export type GeneratedEquippedPreview = {
  jobName: string;
  rank: string | null;
  variant: 1 | 2;
  equipState: "right";
  weaponName: string | null;
  shieldName: string | null;
};

/**
 * The shared loadout renderer's inputs for a replay human whose raw `appearanceInputs.imgIds` are
 * absent (the builder publishes job/rank/gender/weapon/shield; it does not invent the 17-slot
 * native array). Returns null when the replay carries no job identity, so the caller keeps its
 * labelled placeholder instead of guessing a character.
 */
export function generatedEquippedPreview(unit: {
  human: boolean;
  jobId?: string;
  rank?: string | null;
  gender?: number;
  weaponId: number;
  shieldId?: number | null;
}): GeneratedEquippedPreview | null {
  if (!unit.human || !unit.jobId) return null;
  return {
    jobName: unit.jobId,
    rank: unit.rank ?? null,
    variant: unit.gender === 1 ? 2 : 1,
    equipState: "right",
    weaponName: unit.weaponId ? EQUIPMENT_BY_ID.get(unit.weaponId)?.name ?? null : null,
    shieldName: unit.shieldId ? EQUIPMENT_BY_ID.get(unit.shieldId)?.name ?? null : null,
  };
}

/* ------------------------------------------------------------------ */
/* Resolved render paths: what the stage really draws per fighter       */
/* ------------------------------------------------------------------ */

/**
 * How one fighter actually resolves on the shared stage. These are exactly the render branches:
 *  * `own-art` - a monster from its recovered sheet, or a human from the native clip its own
 *    `appearanceInputs.imgIds` select;
 *  * `loadout-preview` - a human with no raw appearance inputs but a known job identity, drawn by the
 *    shared loadout renderer from job/rank/gender plus the equipped weapon and shield;
 *  * `placeholder` - neither is available, so the labelled placeholder is shown.
 *
 * A fighter in the middle case IS drawn, so it must never be counted as a placeholder: the old
 * counting rule keyed on absent `imgIds` alone and called the Wizard/Rancher previews placeholders.
 */
export type GeneratedFighterRenderPath = "own-art" | "loadout-preview" | "placeholder";

export type GeneratedRenderTally = {
  /** fighters drawn from their own recovered art (monster sheet or native human clip) */
  ownArt: number;
  /** humans drawn by the shared loadout renderer from job/rank/gender + equipment */
  loadoutPreview: number;
  /** fighters that really do show the labelled placeholder, with the reason and the missing side */
  placeholders: Array<{
    unitId: string;
    side: "ally" | "enemy";
    /** what is absent for this fighter */
    missing: "human-appearance" | "monster-art";
  }>;
  placeholderCount: number;
};

/** One fighter's resolved render path, from the same inputs the stage branches on. */
export function generatedFighterRenderPath(
  unit: GeneratedUnitView,
  hasMonsterArt: (monsterId: number | null) => boolean,
  /** the baked job/equipment rules, so a builder human resolves exactly as the stage resolves it */
  rules?: GeneratedHumanRules | null,
): GeneratedFighterRenderPath {
  if (!unit.human) return hasMonsterArt(unit.monsterId) ? "own-art" : "placeholder";
  if (generatedHumanCharacter(unit, rules)) return "own-art";
  return generatedEquippedPreview(unit) ? "loadout-preview" : "placeholder";
}

/**
 * The roster tallied by resolved render path. The stage note and the art warning read this, so a
 * fighter the shared loadout renderer draws is never reported to the player as a placeholder.
 */
export function generatedRenderTally(
  units: ReadonlyArray<GeneratedUnitView>,
  hasMonsterArt: (monsterId: number | null) => boolean,
  rules?: GeneratedHumanRules | null,
): GeneratedRenderTally {
  const tally: GeneratedRenderTally = { ownArt: 0, loadoutPreview: 0, placeholders: [], placeholderCount: 0 };
  for (const unit of units) {
    const path = generatedFighterRenderPath(unit, hasMonsterArt, rules);
    if (path === "own-art") tally.ownArt += 1;
    else if (path === "loadout-preview") tally.loadoutPreview += 1;
    else {
      tally.placeholders.push({
        unitId: unit.unitId,
        side: unit.side,
        missing: unit.human ? "human-appearance" : "monster-art",
      });
    }
  }
  tally.placeholderCount = tally.placeholders.length;
  return tally;
}
