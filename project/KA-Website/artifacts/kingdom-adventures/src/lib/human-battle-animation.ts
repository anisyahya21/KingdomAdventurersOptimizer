import humanBattleAnimationJson from "@/game-data/human-battle-animation.json";

import {
  HUMAN_IDLE_GROUP,
  type HumanIdleCharacter,
  type HumanIdleLine,
} from "@/lib/human-battle-idle";

/**
 * Native human battle animation (PASS 15 COMMAND 15.2).
 *
 * The clips in `src/game-data/human-battle-animation.json` are decoded 1:1 from the **intact**
 * originals in `RE-evidence/20260912-combat/chara-animation-original/` by
 * `KA-Website/tools/recovery/export_human_battle_animation.py`. Every SEB line keeps its own sorted
 * key list, because `Seb.GetSprite(frame, line) 0x2351CA0` is a per-line lookup: two lines of the
 * same clip can (and do) select different crops.
 *
 * Which clip a fighter plays:
 *
 *  * idle - `FighterSystem.EnterWaiting 0x15840F0` requests behaviour 3; `humanAnimationSebBases`
 *    (`AnimationSet.cctor 0x165ED2C`) gives base 12, so the clip is `chara/equip_wait_*`.
 *
 *  * attack - `FighterSystem.EnterAttacking 0x15854C0` does not use a constant. It reads
 *    `EquipData.motion` (`+0x64`) off the equipped weapon record (`Entity.get_equip 0x146DDD0` ->
 *    `EquipComponent.get_weapon 0x14C7DB0`) and requests that behaviour. For the two demo weapons
 *    the intact `Equip.txt` master table gives motion 9 (`Guard D`, E/ Fisherman's Pike) and motion
 *    11 (`Archer C`, D/ Bow), i.e. bases 72 (`attack_spear_*`) and 68 (`attack_bow_*`). The three
 *    clips added in the 2026-09-21 ship pass come from the same 43-entry `humanAnimationSebBases`
 *    table read at `skill-combat-constants.json`: behaviour 4 -> base 16 `attack_sword_up.seb` (the
 *    common club/staff/sword/bare-handed motion), behaviour 31 -> base 48 `attack_magic_up.seb`
 *    (the magic-attack / magic-looking-skill behaviour the skill records carry at `+0x4C`), and
 *    behaviour 10 -> base 56 `attack_gun_up.seb`. All three are read 1:1 from the intact
 *    `RE-evidence/20260912-combat/chara-animation-original/` originals; none is an idle stand-in.
 *
 * Timing (CONFIRMED, see the PASS 15 COMMAND 15.1 report):
 *
 *  * `ChangeAnimation 0x165DEA8` sets `SebComponent.frame = 0` and `AnimationSystem.Update
 *    0x14DC884` adds `AnimationComponent.rate` once per rendered update, wrapping at `maxFrame`.
 *    Humans are created with rate 1, so one SEB frame per update = 50 ms at the recovered 20 fps.
 *  * the attack window belongs to the *fighter state*, not to the SEB length: `UpdateAttacking
 *    0x1585568` resolves the attack at blackboard timer 11 and leaves the state at timer >= 20, and
 *    `FighterSystem.Update 0x1583CF8` increments that timer once per rendered update. So the attack
 *    occupies 20 updates (1000 ms) and the hit lands at update 11 (550 ms).
 */

export type HumanClipId =
  | "equipWaitUp"
  | "attackSwordUp"
  | "attackMagicUp"
  | "attackGunUp"
  | "attackSpearUp"
  | "attackBowUp";

/** One SEB key record; `frame` is the key and the line's own list is sorted by it. */
export type HumanSebRecord = {
  line: number;
  frame: number;
  tex: number;
  u: number;
  v: number;
  w: number;
  h: number;
  transX: number;
  transY: number;
  reversU: number;
  reversV: number;
};

export type HumanClip = {
  id: HumanClipId;
  group: string;
  seb: string;
  behaviour: number;
  humanBase: number;
  direction: string;
  why: string;
  source: string;
  maxFrame: number;
  layers: number;
  recordsPerLine: number[];
  bytes: number;
  sha256: string;
  frames: HumanSebRecord[];
  lineResourceGroups: {
    row: number[];
    evidence: "SUPPORTED";
    derivedFrom: string[];
    note: string;
  };
};

type HumanAnimationData = {
  note: string;
  attackWindow: {
    updates: number;
    hitUpdate: number;
    frameMs: number;
    evidence: "CONFIRMED";
    source: string;
  };
  weapons: Record<
    string,
    { id: number; name: string; type: number; res: number; img: number; motion: number }
  >;
  characterWeapons: Record<
    string,
    {
      equipId: number;
      equipName: string;
      equipType: number;
      motion: number;
      clipId: HumanClipId;
      source: string;
    }
  >;
  clips: Record<HumanClipId, HumanClip>;
  reactions: Record<HumanReactionId, HumanReaction>;
  walks: Record<HumanWalkId, HumanWalk>;
};

const DATA = humanBattleAnimationJson as unknown as HumanAnimationData;

export const HUMAN_ANIMATION_NOTE = DATA.note;
export const HUMAN_CLIPS: Record<HumanClipId, HumanClip> = DATA.clips;
export const HUMAN_CHARACTER_WEAPONS = DATA.characterWeapons;
export const HUMAN_WEAPONS = DATA.weapons;

/* ------------------------------------------------------------------ */
/* PASS 15 COMMAND 15.5: damage reaction, knock-down, static down       */
/* ------------------------------------------------------------------ */

export type HumanReactionId = "damage" | "knockDownSit" | "knockDownDown";

/** One `base + direction` variant of a reaction clip, with the `,u` asset flag of `seb.inf`. */
export type HumanReactionDirection = {
  direction: number;
  directionName: string;
  /** `humanBase + direction`, the value `ChangeAnimation` writes to `SebComponent.id` */
  sebId: number;
  seb: string;
  source: string;
  /** the `,u` entry: `Seb.Flip` (+0xB0) mirrors the sprite, i.e. inverts `reversU` */
  flip: boolean;
  maxFrame: number;
  layers: number;
  bytes: number;
  sha256: string;
  frames: HumanSebRecord[];
};

export type HumanReaction = {
  id: HumanReactionId;
  behaviour: number;
  humanBase: number;
  why: string;
  /** `AnimationSet.GetHumanResourceSetType 0x165EA64` for this behaviour (read from its jump table) */
  resourceSetType: number;
  lineResourceGroups: {
    row: number[];
    evidence: "SUPPORTED";
    derivedFrom: string[];
    candidateCount: number;
    note: string;
  };
  directions: HumanReactionDirection[];
};

export const HUMAN_REACTIONS: Record<HumanReactionId, HumanReaction> = DATA.reactions;

/* ------------------------------------------------------------------ */
/* PASS 15 COMMAND 15.11: behavior-2 revival walk                       */
/* ------------------------------------------------------------------ */

export type HumanWalkId = "walk";

export type HumanWalkDirection = {
  direction: number;
  directionName: string;
  /** `humanBase + direction`, written to `SebComponent.id` by `ChangeAnimation` */
  sebId: number;
  seb: string;
  source: string;
  flip: boolean;
  maxFrame: number;
  layers: number;
  bytes: number;
  sha256: string;
  frames: HumanSebRecord[];
};

export type HumanWalk = {
  id: HumanWalkId;
  behaviour: 2;
  humanBase: 8;
  why: string;
  animationRate: 2;
  lineResourceGroups: {
    row: number[];
    evidence: "SUPPORTED";
    derivedFrom: string[];
    candidateCount: number;
    note: string;
  };
  directions: HumanWalkDirection[];
};

export const HUMAN_WALKS: Record<HumanWalkId, HumanWalk> = DATA.walks;

/** `AISystem.MoveBase` speed used by `UpdateMoving 0x15845DC`. */
export const HUMAN_MOVE_SPEED = 4.46;
/** `EnterMoving 0x1584194` writes Animation.rate 2; `ExitMoving 0x15849D8` restores 1. */
export const HUMAN_MOVE_RATE = 2;

/**
 * The damage reaction window: `UpdateDamaging 0x1585F04` returns while `board[4] < 7`, so the state
 * lasts exactly seven rendered updates (350 ms at the recovered 20 fps) and the 6-frame clip loops
 * once inside it.
 */
export const HUMAN_DAMAGE_WINDOW = { updates: 7, maxFrame: 6, frameMs: 50, ms: 350 } as const;

/**
 * The knock-down window from `UpdateKnockingDown 0x1586954`: the direction spins on every update
 * while `board[4] <= 20`, the static `down` pose is requested at `board[4] == 21`, and the state
 * leaves at `board[4] >= 101`.
 */
export const HUMAN_KNOCKDOWN = { spinThrough: 20, downFrom: 21, leavingAt: 101 } as const;

/** The reaction clip for one behaviour, selected by the same `base + direction` rule. */
export function humanReactionDirection(
  reactionId: HumanReactionId,
  direction: number,
): HumanReactionDirection {
  const reaction = HUMAN_REACTIONS[reactionId];
  const index = ((Math.floor(direction) % 4) + 4) % 4;
  const variant = reaction.directions.find((entry) => entry.direction === index);
  if (!variant) {
    throw new Error(`reaction '${reactionId}' has no direction ${index}`);
  }
  return variant;
}

/** The intact `equip_walk` variant selected by the entity direction at `EnterMoving`. */
export function humanWalkDirection(direction: number): HumanWalkDirection {
  const walk = HUMAN_WALKS.walk;
  const index = ((Math.floor(direction) % 4) + 4) % 4;
  const variant = walk.directions.find((entry) => entry.direction === index);
  if (!variant) throw new Error(`walk has no direction ${index}`);
  return variant;
}

/**
 * The recovered damage arc: `UpdateDamaging 0x1585F04` writes
 * `PositionComponent.offsetZ (+0x24) = board[12] + Easing.Parabola(±10, 6, board[4])`, where
 * `board[12]` is the unit's own world z captured by `EnterDamaging` and `ExitDamaging` zeroes the
 * field again. Against the unit's resting placement the baseline cancels, so the visible reaction is
 * the parabola alone: team 0 arcs +10, team 1 arcs -10, over the clip's own 6 frames.
 */
export function humanDamageLift(frame: number, team: number): number {
  const height = team === 0 ? 10 : -10;
  const length = HUMAN_DAMAGE_WINDOW.maxFrame;
  const t = frame / length;
  const value = (t * 4 - t * (t * 4)) * height;
  return Math.trunc(value);
}

/** `Seb.GetSprite` selection inside one explicit record list (used for the reaction variants). */
function reactionRecordAt(
  variant: HumanReactionDirection,
  frame: number,
  line: number,
): HumanSebRecord | null {
  const records = variant.frames.filter((record) => record.line === line);
  if (records.length === 0) return null;
  if (frame < records[0].frame || frame > records[records.length - 1].frame) return null;
  let current = records[0];
  for (const record of records) {
    if (record.frame <= frame) current = record;
    else break;
  }
  return current;
}

/**
 * The per-line records of one reaction clip at one frame, ready for the shared human line resolver.
 * The `,u` variants mirror horizontally, which `Seb.Flip` implements by inverting the sprite's
 * `reversU`; the row is the reaction's own recovered line -> group row.
 */
export function humanReactionLinesAt(
  reactionId: HumanReactionId,
  direction: number,
  frame: number,
): HumanIdleLine[] {
  const reaction = HUMAN_REACTIONS[reactionId];
  const variant = humanReactionDirection(reactionId, direction);
  const row = reaction.lineResourceGroups.row;
  const lines: HumanIdleLine[] = [];
  for (let line = 0; line < variant.layers; line += 1) {
    const res = row[line] ?? -2;
    const part = HUMAN_IDLE_GROUP.partDirs[String(res)] ?? `line ${line}`;
    const record = reactionRecordAt(variant, frame, line);
    if (!record) {
      lines.push({ line, part, res, texId: -1,
        u: 0, v: 0, w: 0, h: 0, transX: 0, transY: 0, reversU: 0, reversV: 0 });
      continue;
    }
    lines.push({
      line,
      part,
      res,
      texId: record.tex,
      u: record.u,
      v: record.v,
      w: record.w,
      h: record.h,
      transX: record.transX,
      transY: record.transY,
      reversU: (record.reversU ^ (variant.flip ? 1 : 0)) & 1,
      reversV: record.reversV,
    });
  }
  return lines;
}

/** `Seb.GetSprite` selection for one walk direction variant. */
function walkRecordAt(
  variant: HumanWalkDirection,
  frame: number,
  line: number,
): HumanSebRecord | null {
  const records = variant.frames.filter((record) => record.line === line);
  if (records.length === 0) return null;
  if (frame < records[0].frame || frame > records[records.length - 1].frame) return null;
  let current = records[0];
  for (const record of records) {
    if (record.frame <= frame) current = record;
    else break;
  }
  return current;
}

/** The per-line `equip_walk` records for the current moving direction and frame. */
export function humanWalkLinesAt(direction: number, frame: number): HumanIdleLine[] {
  const walk = HUMAN_WALKS.walk;
  const variant = humanWalkDirection(direction);
  const row = walk.lineResourceGroups.row;
  const lines: HumanIdleLine[] = [];
  for (let line = 0; line < variant.layers; line += 1) {
    const res = row[line] ?? -2;
    const part = HUMAN_IDLE_GROUP.partDirs[String(res)] ?? `line ${line}`;
    const record = walkRecordAt(variant, frame, line);
    if (!record) {
      lines.push({ line, part, res, texId: -1,
        u: 0, v: 0, w: 0, h: 0, transX: 0, transY: 0, reversU: 0, reversV: 0 });
      continue;
    }
    lines.push({
      line,
      part,
      res,
      texId: record.tex,
      u: record.u,
      v: record.v,
      w: record.w,
      h: record.h,
      transX: record.transX,
      transY: record.transY,
      reversU: (record.reversU ^ (variant.flip ? 1 : 0)) & 1,
      reversV: record.reversV,
    });
  }
  return lines;
}

/** The six visual states the recovery distinguishes for a human fighter. */
export type HumanNativePhase =
  | "wait"
  | "attack"
  | "damage"
  | "knockdown"
  | "down"
  | "revival-moving"
  | "leaving";

/** One world-space point in the recovered battle coordinate system. */
export type HumanWorldPosition = { x: number; y: number; z: number };

export type HumanRevivalState =
  | "revival-moving"
  | "unsupported-during-leaving"
  | "complete";

export type HumanMoveTarget = {
  cell: { column: number; row: number };
  world: HumanWorldPosition;
};

export type HumanRevivalVisual = {
  state: HumanRevivalState;
  /** updates since `EnterMoving` (state 2) */
  moveFrame: number;
  world: HumanWorldPosition;
  target: HumanWorldPosition;
  targetCell: { column: number; row: number };
  direction: number;
  flip: boolean;
  sebId: number;
  seb: string;
  frame: number;
  maxFrame: number;
  rate: number;
  arrived: boolean;
};

/** `FighterSystem.GetPath 0x1584310` for the recovered L-shaped moving path. */
function humanFighterPath(
  start: { x: number; z: number },
  target: { x: number; z: number },
): { x: number; z: number }[] {
  const a1 = target.x - (target.x + 1);
  const b0 = start.z + 1 - start.z;
  const det = -b0 * a1;
  const c0 = start.x * -b0;
  const c1 = -a1 * target.z;
  const crossX = det === 0 ? start.x : (a1 * c0) / det;
  const crossZ = det === 0 ? target.z : (b0 * c1) / det;
  return [
    { x: Math.trunc(crossX), z: Math.trunc(crossZ) },
    { x: Math.trunc(target.x), z: Math.trunc(target.z) },
  ];
}

/** `AISystem.MoveBase 0x148C2E0` at the recovered 4.46 speed, one update per call. */
function humanMoveAt(
  start: HumanWorldPosition,
  target: HumanWorldPosition,
  frame: number,
): { world: HumanWorldPosition; arrived: boolean } {
  const path = humanFighterPath({ x: start.x, z: start.z }, { x: target.x, z: target.z });
  let x = start.x;
  let z = start.z;
  let arrived = false;
  for (let update = 0; update < frame; update += 1) {
    if (path.length === 0) {
      x = target.x;
      z = target.z;
      arrived = true;
      break;
    }
    const waypoint = path[0];
    const dx = waypoint.x - x;
    const dz = waypoint.z - z;
    const distance = Math.hypot(dx, dz);
    if (distance * distance <= HUMAN_MOVE_SPEED * HUMAN_MOVE_SPEED) {
      x = waypoint.x;
      z = waypoint.z;
      path.shift();
      if (path.length === 0) arrived = true;
      continue;
    }
    const step = Math.min(HUMAN_MOVE_SPEED, distance);
    x += (dx / distance) * step;
    z += (dz / distance) * step;
  }
  if (path.length === 0) {
    x = target.x;
    z = target.z;
    arrived = true;
  }
  return { world: { x, y: 0, z }, arrived };
}

/** The state-2 moving visual: `EnterMoving` -> `UpdateMoving` -> `ExitMoving`. */
export function humanRevivalVisual(input: {
  start: HumanWorldPosition;
  target: HumanMoveTarget;
  team: number;
  tick: number;
  startTick: number;
  walkDirection: number;
}): HumanRevivalVisual {
  const moveFrame = Math.max(0, Math.trunc(input.tick - input.startTick));
  const movement = humanMoveAt(input.start, input.target.world, moveFrame);
  if (movement.arrived) {
    const direction = input.team === 0 ? 0 : 2;
    return {
      state: "complete",
      moveFrame,
      world: input.target.world,
      target: input.target.world,
      targetCell: input.target.cell,
      direction,
      flip: false,
      sebId: 0,
      seb: "",
      frame: 0,
      maxFrame: 1,
      rate: 1,
      arrived: true,
    };
  }
  const walk = humanWalkDirection(input.walkDirection);
  return {
    state: "revival-moving",
    moveFrame,
    world: movement.world,
    target: input.target.world,
    targetCell: input.target.cell,
    direction: input.walkDirection,
    flip: walk.flip,
    sebId: walk.sebId,
    seb: walk.seb,
    frame: (moveFrame * HUMAN_MOVE_RATE) % Math.max(1, walk.maxFrame),
    maxFrame: walk.maxFrame,
    rate: HUMAN_MOVE_RATE,
    arrived: false,
  };
}

/** `FighterSystem.GetRowOffset(int) 0x1588254` = `max(3, count//5 + 1)`. */
export function humanLeavingRowOffset(rivalTeamMemberCount: number): number {
  return Math.max(
    HUMAN_LEAVING.minRowOffset,
    Math.trunc(rivalTeamMemberCount / HUMAN_LEAVING.columns) + 1,
  );
}

/**
 * PASS 15 COMMAND 15.9: the leaving fighter is its own kind-10 projectile.
 *
 * `FireProjectile 0x14832D0` turns the fighter's kind (10) into `type = ClampMin(kind, 1)`, computes
 * the straight-line distance, and passes `Entity.AddProjectile` these recovered values:
 * `speed = trunc(distance / type)`, the parabola height `clamp(trunc(distance^2 / 100), 20, 100)`,
 * and `frame = 0`. `ProjectileSystem` then integrates X/Z by one `type`-sized step per update and
 * writes `start.y + Parabola(height, length, frame)` directly to Y. The flight length is
 * `trunc(distance / type)` updates, not the parabola height; the two values are separate native
 * fields and were easy to confuse in the 15.4 prose.
 */
export const HUMAN_LEAVING = {
  kind: 10,
  cellSize: 24,
  columns: 5,
  minRowOffset: 3,
  minHeight: 20,
  maxHeight: 100,
} as const;

export type HumanLeavingFlight = {
  start: HumanWorldPosition;
  end: HumanWorldPosition;
  distance: number;
  /** parabola height passed to the projectile component */
  height: number;
  /**
   * The COMMAND 15.9 `maxFrame` value: `clamp(trunc(distance^2 / 100), 20, 100)`.
   *
   * The validated native projectile layer passes this value as the parabola height
   * (`AddProjectile` w2) and uses `trunc(distance / type)` as the actual flight length
   * (`w3`). Both values are kept distinct so the requested metadata is exact without changing
   * the validated trajectory.
   */
  maxFrame: number;
  /** number of projectile updates before impact (`trunc(distance / type)`) */
  updates: number;
  /** 0-based updates since state 8 entered */
  frame: number;
  impacted: boolean;
  /** live world position for depth sorting and the isometric draw */
  world: HumanWorldPosition;
};

/**
 * The generic off-field destination recovered in COMMAND 15.7:
 * `g = board[7] + 100`, split into row/column as `GridToCellXi/Yi` do, then converted back to
 * world X/Z with the team's direction and the shared `GetRowOffset()`.
 */
export function humanLeavingDestination(
  cell: { column: number; row: number },
  team: number,
  /**
   * The member count of the fixed rival slot `teams[1]`, as `get_RivalTeam 0x1582048` returns it.
   * For a team-0 fighter that is the opponent team; for a team-1 fighter it is its own team slot.
   */
  rivalTeamMemberCount: number,
): HumanWorldPosition {
  const rowOffset = humanLeavingRowOffset(rivalTeamMemberCount);
  const grid = cell.row * HUMAN_LEAVING.columns + cell.column;
  const queuedGrid = grid + 100;
  const row = Math.trunc(queuedGrid / HUMAN_LEAVING.columns);
  const column = queuedGrid - row * HUMAN_LEAVING.columns;
  return {
    x: column * HUMAN_LEAVING.cellSize,
    y: 0,
    z:
      (rowOffset +
        (team === 0 ? 1 : 0) +
        (team === 0 ? row : -row)) *
      HUMAN_LEAVING.cellSize,
  };
}

/** `Easing.Parabola 0x1444988`: integer truncation after the float arithmetic. */
export function humanParabola(height: number, length: number, frame: number): number {
  if (length <= 0) return 0;
  const t = frame / length;
  return Math.trunc((t * 4 - t * (t * 4)) * height);
}

/**
 * One visual frame of the kind-10 self-projectile. The native system updates the projectile before
 * `MoveSystem` integrates its velocity, so the first rendered update already sits one step along
 * the path; at and after the recovered flight length the unit is at `end` (the projectile component
 * is removed, but the fighter remains there in state 8).
 */
export function humanLeavingFlightAt(input: {
  cell: { column: number; row: number };
  team: number;
  /** the fixed `teams[1]` count; this is the native `GetRowOffset()` source for every caller */
  rivalTeamMemberCount: number;
  frame: number;
}): HumanLeavingFlight {
  const { cell, team, rivalTeamMemberCount, frame } = input;
  const start: HumanWorldPosition = {
    x: cell.column * HUMAN_LEAVING.cellSize,
    y: 0,
    z: cell.row * HUMAN_LEAVING.cellSize,
  };
  const end = humanLeavingDestination(cell, team, rivalTeamMemberCount);
  const dx = end.x - start.x;
  const dz = end.z - start.z;
  const distance = Math.sqrt(dx * dx + dz * dz);
  const height = Math.min(
    HUMAN_LEAVING.maxHeight,
    Math.max(HUMAN_LEAVING.minHeight, Math.trunc((distance * distance) / 100)),
  );
  const speed = Math.max(1, Math.trunc(distance / HUMAN_LEAVING.kind));
  const updates = Math.max(0, Math.trunc(distance / speed));
  const current = Math.max(0, Math.trunc(frame));
  const impacted = current >= updates;
  const applied = impacted ? updates : current + 1;
  const nx = distance > 0 ? dx / distance : 0;
  const nz = distance > 0 ? dz / distance : 0;
  const world: HumanWorldPosition = impacted
    ? end
    : {
        x: start.x + nx * speed * applied,
        y: humanParabola(height, updates, applied),
        z: start.z + nz * speed * applied,
      };
  return { start, end, distance, height, maxFrame: height, updates, frame: current, impacted, world };
}

export type HumanNativeVisual = {
  phase: HumanNativePhase;
  /** the value the debug/test hook exposes: identical to `phase` */
  nativeState: HumanNativePhase;
  clipId: HumanClipId | null;
  reactionId: HumanReactionId | null;
  behaviour: number;
  /** `base + direction` written to `SebComponent.id` */
  sebId: number;
  seb: string;
  direction: number;
  flip: boolean;
  frame: number;
  maxFrame: number;
  /** updates since the damage reaction started (null outside it) */
  reactionUpdate: number | null;
  /** native knock-down timer (`board[4]`), 1-based, null before the knock-down */
  knockdownUpdate: number | null;
  attackUpdate: number | null;
  /** the recovered `PositionComponent.offsetZ` arc while damaged */
  damageLiftZ: number;
  /** the live kind-10 flight while the unit is in state 8, otherwise null */
  leaving: HumanLeavingFlight | null;
  /** state-2 revival walk when a cure interrupts damage/knock-down/down */
  revival?: HumanRevivalVisual | null;
  trace: string;
};

/**
 * The whole human visual state machine, in native update units.
 *
 * `damageStartTick` is the update the reaction begins on: for a hit from one of the implemented human
 * attacks that is the attacker's `attackStartTick + 11` (`UpdateAttacking 0x1585568` resolves the hit
 * at timer 11); for any source whose hit timing is not recovered it is the update the replay event
 * became current. `lethal` is the replay's `ko` flag and is the only thing that separates the two
 * post-damage paths.
 *
 * Native order: damage for 7 updates, then either back to EQUIP_WAIT (surviving) or the knock-down,
 * whose `board[4]` timer spins the direction on 1..20, requests behaviour 7 at 21 and leaves the state
 * at 101. COMMAND 15.9 continues into state 8: the unit becomes its own kind-10 projectile, its
 * live world position carries the depth draw, and it keeps alternating the static down asset / its
 * `,u` mirror because `UpdateLeaving` adds 2 to the direction every update.
 */
export function humanNativeVisual(input: {
  character: HumanIdleCharacter;
  tick: number;
  team: number;
  /** the cell the unit occupies when state 8 begins; `board[7]` is rebuilt from this cell */
  startCell: { column: number; row: number };
  /** the fixed `teams[1]` member count used by `GetRowOffset()` for placement, camera and leaving */
  rivalTeamMemberCount: number;
  /** the facing the unit had when the reaction started (native keeps spinning from there) */
  entryDirection: number;
  attackStartTick: number | null;
  damageStartTick: number | null;
  lethal: boolean;
  reviveStartTick?: number | null;
  reviveStartWorld?: HumanWorldPosition | null;
  reviveTarget?: HumanMoveTarget | null;
  reviveDirection?: number;
}): HumanNativeVisual {
  const {
    character,
    tick,
    team,
    startCell,
    rivalTeamMemberCount,
    entryDirection,
    attackStartTick,
    damageStartTick,
    lethal,
    reviveStartTick = null,
    reviveStartWorld = null,
    reviveTarget = null,
    reviveDirection = 0,
  } = input;
  const idle = HUMAN_IDLE_CLIP;
  const waitAt = (sinceTick: number, note: string): HumanNativeVisual => {
    const frame = humanClipFrame(idle, sinceTick);
    return {
      phase: "wait", nativeState: "wait", clipId: idle.id, reactionId: null,
      behaviour: idle.behaviour, sebId: idle.humanBase + 0, seb: idle.seb,
      direction: 0, flip: false, frame, maxFrame: idle.maxFrame,
      reactionUpdate: null, knockdownUpdate: null, attackUpdate: null, damageLiftZ: 0,
      leaving: null,
      trace: `${idle.seb} f${frame}/${idle.maxFrame} ${note}`,
    };
  };

  if (reviveStartTick !== null && reviveTarget) {
    const revival = humanRevivalVisual({
      start:
        reviveStartWorld ?? {
          x: startCell.column * 24,
          y: 0,
          z: startCell.row * 24,
        },
      target: reviveTarget,
      team,
      tick,
      startTick: reviveStartTick,
      walkDirection: reviveDirection,
    });
    if (!revival.arrived) {
      return {
        phase: "revival-moving",
        nativeState: "revival-moving",
        clipId: null,
        reactionId: null,
        behaviour: HUMAN_WALKS.walk.behaviour,
        sebId: revival.sebId,
        seb: revival.seb,
        direction: revival.direction,
        flip: revival.flip,
        frame: revival.frame,
        maxFrame: revival.maxFrame,
        reactionUpdate: null,
        knockdownUpdate: null,
        attackUpdate: null,
        damageLiftZ: 0,
        leaving: null,
        revival,
        trace:
          `${revival.seb} f${revival.frame}/${revival.maxFrame} ` +
          `move${revival.moveFrame} -> (${revival.target.x},${revival.target.z})`,
      };
    }
    return { ...waitAt(0, "after revival"), revival };
  }

  if (damageStartTick === null) {
    if (attackStartTick !== null && tick - attackStartTick < HUMAN_ATTACK_WINDOW.updates) {
      const attackUpdate = tick - attackStartTick;
      const clip = humanAttackClipForCharacter(character);
      const frame = humanClipFrame(clip, attackUpdate);
      return {
        phase: "attack", nativeState: "attack", clipId: clip.id, reactionId: null,
        behaviour: clip.behaviour, sebId: clip.humanBase, seb: clip.seb,
        direction: 0, flip: false, frame, maxFrame: clip.maxFrame,
        reactionUpdate: null, knockdownUpdate: null, attackUpdate, damageLiftZ: 0,
        leaving: null,
        trace:
          `${clip.seb} f${frame}/${clip.maxFrame} attackUpdate${attackUpdate}` +
          (attackUpdate === HUMAN_ATTACK_WINDOW.hitUpdate
            ? ` native-hit-update ${HUMAN_ATTACK_WINDOW.hitUpdate}`
            : ""),
      };
    }
    return waitAt(tick, `update${tick}`);
  }

  const sinceDamage = tick - damageStartTick;
  if (sinceDamage < HUMAN_DAMAGE_WINDOW.updates) {
    const frame = sinceDamage % HUMAN_DAMAGE_WINDOW.maxFrame;
    const variant = humanReactionDirection("damage", entryDirection);
    return {
      phase: "damage", nativeState: "damage", clipId: null, reactionId: "damage",
      behaviour: HUMAN_REACTIONS.damage.behaviour, sebId: variant.sebId, seb: variant.seb,
      direction: entryDirection, flip: variant.flip, frame, maxFrame: variant.maxFrame,
      reactionUpdate: sinceDamage, knockdownUpdate: null, attackUpdate: null,
      damageLiftZ: humanDamageLift(frame, team),
      leaving: null,
      trace:
        `${variant.seb} f${frame}/${variant.maxFrame} reactionUpdate${sinceDamage} ` +
        `offsetZ${humanDamageLift(frame, team)}`,
    };
  }

  if (!lethal) {
    return waitAt(sinceDamage - HUMAN_DAMAGE_WINDOW.updates, "after damage");
  }

  // Native knock-down timer: the state is entered the update the damage state exits, so the first
  // knock-down update carries timer 1.
  const knockdownUpdate = sinceDamage - HUMAN_DAMAGE_WINDOW.updates + 1;
  if (knockdownUpdate < HUMAN_KNOCKDOWN.downFrom) {
    const spin = Math.min(knockdownUpdate, HUMAN_KNOCKDOWN.spinThrough);
    const direction = (((entryDirection + spin) % 4) + 4) % 4;
    const variant = humanReactionDirection("knockDownSit", direction);
    const frame = (knockdownUpdate - 1) % Math.max(1, variant.maxFrame);
    return {
      phase: "knockdown", nativeState: "knockdown", clipId: null, reactionId: "knockDownSit",
      behaviour: HUMAN_REACTIONS.knockDownSit.behaviour, sebId: variant.sebId, seb: variant.seb,
      direction, flip: variant.flip, frame, maxFrame: variant.maxFrame,
      reactionUpdate: null, knockdownUpdate, attackUpdate: null, damageLiftZ: 0,
      leaving: null,
      trace: `${variant.seb} f${frame}/${variant.maxFrame} kd${knockdownUpdate} dir${direction}`,
    };
  }

  // The settled pose: the direction stopped at spinThrough quarter turns and behaviour 7 is a
  // one-frame clip, so the pose is static until the leaving state.
  const settledDirection = (((entryDirection + HUMAN_KNOCKDOWN.spinThrough) % 4) + 4) % 4;
  const down = humanReactionDirection("knockDownDown", settledDirection);
  if (knockdownUpdate < HUMAN_KNOCKDOWN.leavingAt) {
    return {
      phase: "down", nativeState: "down", clipId: null, reactionId: "knockDownDown",
      behaviour: HUMAN_REACTIONS.knockDownDown.behaviour, sebId: down.sebId, seb: down.seb,
      direction: settledDirection, flip: down.flip, frame: 0, maxFrame: down.maxFrame,
      reactionUpdate: null, knockdownUpdate, attackUpdate: null, damageLiftZ: 0,
      leaving: null,
      trace: `${down.seb} f0/${down.maxFrame} kd${knockdownUpdate} dir${settledDirection}`,
    };
  }

  /*
   * State 8. `EnterLeaving` makes no ChangeAnimation call and `FireProjectile` is called with its
   * AddAnimation flag zero, so the one-frame down pose stays selected while the fighter itself
   * moves. `UpdateLeaving 0x15870CC` then adds 2 to the direction each update, alternating the
   * `down_right.seb` asset and its `,u` mirror.
   */
  const leavingFrame = knockdownUpdate - HUMAN_KNOCKDOWN.leavingAt;
  const flight = humanLeavingFlightAt({
    cell: startCell,
    team,
    rivalTeamMemberCount,
    frame: leavingFrame,
  });
  const leavingDirection = (((settledDirection + 2 * leavingFrame) % 4) + 4) % 4;
  const leavingPose = humanReactionDirection("knockDownDown", leavingDirection);
  return {
    phase: "leaving", nativeState: "leaving", clipId: null,
    reactionId: "knockDownDown",
    behaviour: HUMAN_REACTIONS.knockDownDown.behaviour,
    sebId: leavingPose.sebId,
    seb: leavingPose.seb,
    direction: leavingDirection,
    flip: leavingPose.flip,
    frame: 0,
    maxFrame: leavingPose.maxFrame,
    reactionUpdate: null, knockdownUpdate, attackUpdate: null, damageLiftZ: 0,
    leaving: flight,
    trace:
      `${leavingPose.seb} f0 static kd${knockdownUpdate} leaving${leavingFrame}` +
      `/${flight.updates} world(${flight.world.x.toFixed(1)},${flight.world.y},${flight.world.z.toFixed(1)})` +
      (flight.impacted ? " impacted" : ""),
  };
}

/**
 * The recovered attack window: 20 updates of attack state, the damage at update 11
 * (`FighterSystem.UpdateAttacking 0x1585568`), one update per rendered frame at the form's 20 fps.
 */
export const HUMAN_ATTACK_WINDOW = {
  ...DATA.attackWindow,
  windowMs: DATA.attackWindow.updates * DATA.attackWindow.frameMs,
  hitMs: DATA.attackWindow.hitUpdate * DATA.attackWindow.frameMs,
} as const;

/** Native loop: `AnimationSystem.Update` wraps the frame at the clip's own `maxFrame`. */
export function humanClipFrame(clip: HumanClip, update: number): number {
  const loop = Math.max(1, clip.maxFrame);
  return ((Math.floor(update) % loop) + loop) % loop;
}

/** Records of one SEB line, in file order; their `frame` values are the line's key list. */
export function humanLineRecords(clip: HumanClip, line: number): HumanSebRecord[] {
  return clip.frames.filter((record) => record.line === line);
}

/**
 * `Seb.GetSprite(frame, line) 0x2351CA0` for one line, or null when that line draws nothing.
 *
 * The rule is the one already recovered for the monster clips and re-read for this command:
 *   * frame < keys[first] or frame > keys[last]  -> no sprite for that line;
 *   * frame == key                               -> that record, verbatim;
 *   * keys[i-1] < frame < keys[i]                -> the previous record's values persist while its
 *     texId >= 0 (only records with a negative texId tween their transform fields, which the
 *     original code does to blend toward the runtime-supplied image).
 */
export function humanSpriteAt(clip: HumanClip, frame: number, line: number): HumanSebRecord | null {
  const records = humanLineRecords(clip, line);
  if (records.length === 0) return null;
  if (frame < records[0].frame || frame > records[records.length - 1].frame) return null;
  let current = records[0];
  for (const record of records) {
    if (record.frame <= frame) current = record;
    else break;
  }
  return current;
}

/**
 * The 14 per-line records a clip shows at one (already wrapped) frame, in the shape the shared
 * human line resolver consumes.
 *
 * `res` comes from the clip's own line -> group row: the attack clips carry the weapon on line 2 and
 * the face on line 7, where EQUIP_WAIT has the weapon on line 12 and the face on line 6, so the row
 * has to follow the clip. The row is the unique recovered `HumanResourceSet` row consistent with the
 * clip's crops and both demo inputs (`export_human_battle_animation.py` proves the uniqueness).
 * Lines whose record is missing, or whose record is a runtime-supplied line (negative texId), are
 * still returned - the shared resolver reports them as skipped rather than substituting anything.
 */
export function humanLineRecordsAt(clip: HumanClip, frame: number): HumanIdleLine[] {
  const row = clip.lineResourceGroups.row;
  const lines: HumanIdleLine[] = [];
  for (let line = 0; line < clip.layers; line += 1) {
    const res = row[line] ?? -2;
    const record = humanSpriteAt(clip, frame, line);
    const part = HUMAN_IDLE_GROUP.partDirs[String(res)] ?? `line ${line}`;
    if (!record) {
      lines.push({
        line, part, res, texId: -1,
        u: 0, v: 0, w: 0, h: 0, transX: 0, transY: 0, reversU: 0, reversV: 0,
      });
      continue;
    }
    lines.push({
      line,
      part,
      res,
      texId: record.tex,
      u: record.u,
      v: record.v,
      w: record.w,
      h: record.h,
      transX: record.transX,
      transY: record.transY,
      reversU: record.reversU,
      reversV: record.reversV,
    });
  }
  return lines;
}

export type HumanAnimationPhase = "equip-wait" | "attack";

export type HumanAnimationState = {
  phase: HumanAnimationPhase;
  clipId: HumanClipId;
  clip: HumanClip;
  /** SEB frame inside the clip, already wrapped at `maxFrame` */
  frame: number;
  /** updates since the current clip started (native `AnimationComponent` phase) */
  clipUpdate: number;
  /** updates since the attack visual started, or null while idling */
  attackUpdate: number | null;
  /** true exactly on the update the native attack resolves (update 11) */
  atNativeHitUpdate: boolean;
  /** true on the update the native attack state ends (update 20) */
  atAttackEnd: boolean;
  /** data for the debug/test surface */
  trace: string;
};

/** The attack clip a demo character plays, from its equipped weapon's `EquipData.motion`. */
export function humanAttackClipForCharacter(character: HumanIdleCharacter): HumanClip {
  const weapon = HUMAN_CHARACTER_WEAPONS[character.id];
  if (!weapon) {
    throw new Error(
      `no recovered weapon motion for human character '${character.id}': ` +
        "add it to WEAPONS in export_human_battle_animation.py",
    );
  }
  return HUMAN_CLIPS[weapon.clipId];
}

/** The idle clip (`EnterWaiting 0x15840F0` -> behaviour 3 -> base 12 -> `equip_wait_*`). */
export const HUMAN_IDLE_CLIP = HUMAN_CLIPS.equipWaitUp;

/**
 * The visual state of a human ally at one native update.
 *
 * `attackStartTick` is the update at which the replay's current beat became this unit's attack
 * event; `null` means the unit is idling. The attack lasts exactly `HUMAN_ATTACK_WINDOW.updates`
 * updates and then the unit is back on EQUIP_WAIT - the state machine's own window, never the SEB
 * length.
 */
export function humanAnimationState(
  character: HumanIdleCharacter,
  tick: number,
  attackStartTick: number | null,
): HumanAnimationState {
  const idleClip = HUMAN_IDLE_CLIP;
  if (attackStartTick === null) {
    const frame = humanClipFrame(idleClip, tick);
    return {
      phase: "equip-wait",
      clipId: idleClip.id,
      clip: idleClip,
      frame,
      clipUpdate: tick,
      attackUpdate: null,
      atNativeHitUpdate: false,
      atAttackEnd: false,
      trace: `${idleClip.seb} f${frame}/${idleClip.maxFrame} update${tick}`,
    };
  }
  const attackUpdate = tick - attackStartTick;
  if (attackUpdate >= HUMAN_ATTACK_WINDOW.updates) {
    // Native returns to EQUIP_WAIT when UpdateAttacking sees the timer reach 20; the wait clip is a
    // fresh ChangeAnimation, so its own frame counter restarts at 0 on that update.
    const sinceWait = attackUpdate - HUMAN_ATTACK_WINDOW.updates;
    const frame = humanClipFrame(idleClip, sinceWait);
    return {
      phase: "equip-wait",
      clipId: idleClip.id,
      clip: idleClip,
      frame,
      clipUpdate: sinceWait,
      attackUpdate,
      atNativeHitUpdate: false,
      atAttackEnd: attackUpdate === HUMAN_ATTACK_WINDOW.updates,
      trace: `${idleClip.seb} f${frame}/${idleClip.maxFrame} update${tick} (attack window closed)`,
    };
  }
  const clip = humanAttackClipForCharacter(character);
  const frame = humanClipFrame(clip, attackUpdate);
  return {
    phase: "attack",
    clipId: clip.id,
    clip,
    frame,
    clipUpdate: attackUpdate,
    attackUpdate,
    atNativeHitUpdate: attackUpdate === HUMAN_ATTACK_WINDOW.hitUpdate,
    atAttackEnd: false,
    trace:
      `${clip.seb} f${frame}/${clip.maxFrame} attackUpdate${attackUpdate}` +
      (attackUpdate === HUMAN_ATTACK_WINDOW.hitUpdate
        ? ` native-hit-update ${HUMAN_ATTACK_WINDOW.hitUpdate}`
        : ""),
  };
}
