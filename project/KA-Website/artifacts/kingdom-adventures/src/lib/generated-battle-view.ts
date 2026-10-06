import {
  attachReplayVisualSetup,
  foldBattleReplayTimeline,
  type BattleReplayEvent,
  type BattleReplayResult,
  type BattleReplayRewardEntitlement,
  type BattleReplayUnit,
  type ReplayUnitVisual,
  type ReplayVisualSetup,
} from "@/lib/battle-replay-result";
import { EQUIPMENT_BY_ID, type JobRank } from "@/lib/battle-setup";
import { COMBAT_JOB_ROWS_BY_NAME } from "@/lib/battle-legality";
import { isMostFrontUnit, type FighterVitals } from "@/lib/native-fighter-gauges";
import {
  BATTLE_DIRECTIONS,
  nativeViewPos,
  nativeWorldViewPos,
  monsterClipForDirection,
  type DecodedClip,
  type MonsterDirectionId,
} from "@/lib/battle-replay";
import {
  HUMAN_CLIPS,
  HUMAN_DAMAGE_WINDOW,
  HUMAN_KNOCKDOWN,
  HUMAN_LEAVING,
  humanClipFrame,
  humanLineRecordsAt,
  humanParabola,
  humanReactionDirection,
  humanReactionLinesAt,
  humanWalkDirection,
  humanWalkLinesAt,
  type HumanClipId,
} from "@/lib/human-battle-animation";
import { HUMAN_IDLE_LINES, type HumanIdleCharacter, type HumanIdleLine } from "@/lib/human-battle-idle";

/**
 * Generated-replay view model - PASS 16 COMMAND 16.9, correctness follow-up.
 *
 * Everything here is derived from the authoritative replay: rosters come from `replay.units`
 * ordered by side and rosterIndex, formation cells come from the replay (initial cell + the
 * `cell_change` events, which the Python contract writes as `[column, row]`), HP/MP come from the
 * param 10/11 maximum plus the real damage/heal/MP events folded by `foldBattleReplayTimeline` and
 * closed by `finalState`, and the visual lifecycle is triggered by the simulator's own events
 * (`state`, `animation_request`, `body_flight`, `projectile_*`, `heal`, status events) using the
 * shared recovered human clips. Nothing is re-simulated and no encounter fixture is consulted.
 */

/** Recovered native fighter states (BKI:5) - the presentation lifecycles already implemented. */
export type GeneratedVisualState =
  | "waiting"
  | "walking"
  | "attacking"
  | "damaging"
  | "knocking_down"
  | "down"
  | "leaving"
  | "revival_moving"
  | "unknown";

export type GeneratedUnitView = {
  unitId: string;
  entityId: number;
  side: "ally" | "enemy";
  rosterIndex: number;
  name: string;
  human: boolean;
  monsterId: number | null;
  kind: "human" | "monster";
  /** visualSetup metadata, joined by unitId (job/rank/gender/appearance) */
  visual?: ReplayUnitVisual;
  jobId?: string;
  rank?: JobRank | string;
  gender?: number;
  /** recovered equipment id of the shield slot (BUILDER-CONTRACT.md additive field), 0/null = none */
  shieldId?: number | null;
  weaponId: number;
  /** recovered EquipData motion of the equipped weapon (null for monsters) */
  weaponMotion: number | null;
  skillIds: number[];
  invocationLevels: number[];
  initialCell: [number, number];
  initialGrid: number;
  startHp: number;
  startMp: number;
  /** Exact native effective maximum; null when the selected engine did not report it. */
  maxHp: number | null;
  maxMp: number | null;
  /** every motion the runner supports and that has a decoded sprite family today */
  animationCoverage: "FULL" | "PARTIAL";
  coverageNotes: string[];
};

export type GeneratedCoverage = {
  /** motions the runner accepts but whose SEB clips are not decoded yet */
  undecodedWeaponClips: number[];
  missingMonsterArt: string[];
  partialHumanAppearance: string[];
  arenaPartial: boolean;
  skillAnimationPending: number;
  warnings: string[];
  enemyArtPartial: boolean;
};

/** A runner world vector (`[x, y, z]`), or null when the payload did not carry one. */
function asVector3(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length < 3) return null;
  const [x, y, z] = value;
  if (typeof x !== "number" || typeof y !== "number" || typeof z !== "number") return null;
  return [x, y, z];
}

/**
 * The runner's own kind-10 body flight (`body_flight`): the fighter is launched from `start` to
 * `end` at `speed` world units per update. Endpoints and speed are the runner's values, so the
 * departure path is read back instead of being inferred from the cell.
 */
export type GeneratedBodyFlight = {
  /** authoritative replay tick the flight was launched on */
  tick: number;
  speed: number;
  start: [number, number, number];
  end: [number, number, number];
};

/** One unit's authoritative state at the end of one replay tick. */
export type GeneratedUnitFrame = {
  hp: number;
  mp: number;
  maxHp: number | null;
  maxMp: number | null;
  state: number;
  stateName: string | null;
  visual: GeneratedVisualState;
  cell: [number, number];
  /** HP from the folded events reached 0 (authoritative), not a dimming heuristic */
  dead: boolean;
  /** the folded state is the leaving lifecycle (state 8) */
  leaving: boolean;
  /** status tags folded from the runner's own status events, sorted */
  status: string[];
  /** the recovered clip the runner's own `animation_request` selected, or null when unsupported */
  clipId: HumanClipId | null;
  /** frame inside that clip at this tick (native loop), 0 when no clip is selected */
  clipFrame: number;
  /** the raw behaviour the runner requested (kept for the DOM contract even when unsupported) */
  clipBehavior: number | null;
  /** the tick the current state began (for the reaction clip frame) */
  stateTick: number;
  /** the newest runner `body_flight` launched for this unit, or null while it stands on its cell */
  flight: GeneratedBodyFlight | null;
};

/** One frame per authoritative replay tick; `events` are all events at that tick in seq order. */
export type GeneratedFrame = {
  /** 0-based frame ordinal (frames are one per tick, including empty ticks) */
  index: number;
  /** the authoritative replay tick */
  tick: number;
  /** first event seq at this tick, or -1 for an empty tick */
  seq: number;
  units: Record<string, GeneratedUnitFrame>;
  events: BattleReplayEvent[];
};

const RUNNER_WEAPON_MOTIONS = new Set([4, 9, 10, 11, 12, 16, 37]);

/** `BATTLE_DIRECTIONS` as the numeric entity direction `ChangeAnimation` adds to the SEB base. */
const DIRECTION_INDEX: Record<MonsterDirectionId, number> = { up: 0, right: 1, down: 2, left: 3 };

/**
 * The recovered human clip table keyed by the SEB base `AnimationSet` writes. It is derived from
 * `HUMAN_CLIPS` itself rather than written out by hand, so a newly decoded clip (an exported
 * `attack_sword_up` for behaviour 4, for example) is picked up without a second list to keep in
 * step - the previous hand-written map named three bases and would silently ignore a fourth.
 */
const CLIP_BY_HUMAN_BASE: Record<number, HumanClipId> = Object.fromEntries(
  (Object.keys(HUMAN_CLIPS) as HumanClipId[]).map((id) => [HUMAN_CLIPS[id].humanBase, id]),
) as Record<number, HumanClipId>;

/**
 * Weapon behaviour (`EquipData.motion`, read by `EnterAttacking 0x15854C0`) -> the attack SEB base
 * `HUMAN_BASES[behaviour]` carries (`combat_animation_selection.combat_clip`).
 */
const ATTACK_BASE_BY_WEAPON_MOTION: Record<number, number> = {
  4: 16,
  9: 72,
  10: 56,
  11: 68,
  // Behaviour 31 is the magic-attack behaviour a skill record carries at `+0x4C`
  // (`EnterUsingSkill 0x1585AC0`); the healer's Heal Maddy (skill 37) requests it.
  31: 48,
};

/**
 * Motions whose attack SEB is actually decoded. Derived from the clip table, not asserted: a motion
 * whose base has no clip is reported as PARTIAL coverage instead of being claimed as decoded and
 * then drawing nothing.
 */
const DECODED_WEAPON_MOTIONS = new Set(
  Object.entries(ATTACK_BASE_BY_WEAPON_MOTION)
    .filter(([, base]) => CLIP_BY_HUMAN_BASE[base] !== undefined)
    .map(([motion]) => Number(motion)),
);

/**
 * The recovered attack clip for one weapon motion, or null when that weapon's attack SEB has not
 * been exported yet. The caller keeps the fighter's idle/reaction clip in that case; nothing is
 * substituted for the missing attack pose.
 */
export function generatedAttackClipId(weaponMotion: number | null): HumanClipId | null {
  if (weaponMotion === null) return null;
  const base = ATTACK_BASE_BY_WEAPON_MOTION[weaponMotion];
  return base === undefined ? null : CLIP_BY_HUMAN_BASE[base] ?? null;
}

/**
 * Resolve a runner `animation_request.clip` to a recovered clip, or null. `ChangeAnimation` writes
 * `base + direction`, so the unit's own entity direction (`BATTLE_DIRECTIONS`: allies 0, opponents
 * 2) has to be subtracted before the base lookup - otherwise an opponent human resolves to no clip
 * at all while its ally counterpart resolves.
 */
export function generatedClipForBase(base: number | null | undefined, direction = 0): HumanClipId | null {
  if (typeof base !== "number") return null;
  const d = ((Math.floor(direction) % 4) + 4) % 4;
  return CLIP_BY_HUMAN_BASE[base - d] ?? CLIP_BY_HUMAN_BASE[base] ?? null;
}

/**
 * The recovered clip a human fighter plays at one frame: the clip the runner's own
 * `animation_request` selected, or - while attacking - the clip the fighter's equipped weapon
 * selects. Null when neither is decoded.
 */
export function generatedFrameClipId(
  unit: GeneratedUnitView,
  unitFrame: GeneratedUnitFrame,
): HumanClipId | null {
  // `EnterAttacking 0x15854C0` picks the attack clip from the equipped weapon's `EquipData.motion`,
  // so while the fighter is attacking that weapon's own decoded clip outranks whatever clip the
  // runner requested: a sweep/skill behaviour whose SEB is not exported must not shadow a weapon
  // pose that is decoded.
  if (unitFrame.visual === "attacking") {
    return generatedAttackClipId(unit.weaponMotion) ?? unitFrame.clipId;
  }
  return unitFrame.clipId;
}

function visualStateFor(
  state: number,
  previous: GeneratedVisualState,
  /**
   * The runner's own `revive` event named this unit and it has not left moving 2 yet. The recovered
   * lifecycle distinguishes the revival walk from plain movement by the revive command, not by the
   * state number alone (`humanAnimationState` takes an explicit revive target), so the event - not
   * the KO history - is what keeps `revival_moving` selected while the fighter walks back.
   */
  revived = false,
): GeneratedVisualState {
  switch (state) {
    case 1:
      return "waiting";
    case 4:
      return "attacking";
    case 6:
      return "damaging";
    case 7:
      return "knocking_down";
    case 8:
      return "leaving";
    case 2:
      return revived ||
        previous === "revival_moving" ||
        previous === "down" ||
        previous === "knocking_down" ||
        previous === "leaving"
        ? "revival_moving"
        : "walking";
    default:
      return previous;
  }
}

/** The exact native HP/MP maximum, or null when the replay payload did not report one. */
function parameterMaximum(unit: BattleReplayUnit | undefined, key: "10" | "11"): number | null {
  const maximum = unit?.parameters?.[key]?.effectiveMaximum;
  return typeof maximum === "number" && Number.isFinite(maximum) && maximum >= 0 ? maximum : null;
}

export function buildGeneratedUnits(
  replay: BattleReplayResult,
  visualSetup?: ReplayVisualSetup,
): GeneratedUnitView[] {
  const withVisuals = attachReplayVisualSetup(replay, visualSetup);
  const visuals = withVisuals.visuals ?? {};
  return withVisuals.units
    .slice()
    .sort((a, b) => (a.side === b.side ? a.rosterIndex - b.rosterIndex : a.side === "ally" ? -1 : 1))
    .map((unit: BattleReplayUnit) => {
      const visual = visuals[unit.unitId];
      const weaponMotion = unit.human
        ? visual?.weaponMotion ?? EQUIPMENT_BY_ID.get(unit.weaponId)?.motion ?? null
        : null;
      const notes: string[] = [];
      if (unit.human && weaponMotion !== null && RUNNER_WEAPON_MOTIONS.has(weaponMotion) && !DECODED_WEAPON_MOTIONS.has(weaponMotion)) {
        notes.push(`weapon motion ${weaponMotion} is accepted by the runner but its SEB clip is not decoded yet`);
      }
      if (!unit.human) {
        notes.push("monster battle art coverage depends on the recovered sheet for this id");
      }
      if (unit.human && !visual?.appearanceInputs?.imgIds?.length) {
        notes.push("human appearance inputs are incomplete (PARTIAL coverage)");
      }
      return {
        unitId: unit.unitId,
        entityId: unit.entityId,
        side: unit.side,
        rosterIndex: unit.rosterIndex,
        name: unit.name,
        human: unit.human,
        monsterId: unit.monsterId,
        kind: unit.kind,
        ...(visual ? { visual } : {}),
        ...(visual?.jobId ? { jobId: visual.jobId } : {}),
        ...(visual?.rank ? { rank: visual.rank as JobRank } : {}),
        ...(visual?.gender === undefined ? {} : { gender: visual.gender }),
        ...(visual?.shieldId === undefined ? {} : { shieldId: visual.shieldId }),
        // The saved loadout's own equipment identity wins over the replay unit's echo of it: the
        // /battle route draws what the player equipped, and the two only disagree on a payload whose
        // visual identity was redirected (the labelled renderer probes).
        weaponId: visual?.weaponId ?? unit.weaponId,
        weaponMotion,
        skillIds: [...unit.skillIds],
        invocationLevels: [...unit.invocationLevels],
        initialCell: [unit.cell[0], unit.cell[1]],
        initialGrid: unit.grid,
        startHp: unit.startHp,
        startMp: unit.startMp,
        maxHp: parameterMaximum(unit, "10"),
        maxMp: parameterMaximum(unit, "11"),
        animationCoverage: notes.length === 0 ? "FULL" : "PARTIAL",
        coverageNotes: notes,
      };
    });
}

/**
 * The subset of `character_sprites/character-rules.json` this module needs to rebuild a fighter's
 * 17-slot native image array from the job record and the equipped items. It is the SAME baked
 * document `loadHumanPartRules()` reads for the part sheets, and `character-renderer.ts` uses for the
 * loadout preview - the battle replay does not grow a second equipment or job table.
 */
export type GeneratedHumanRules = {
  jobs: Array<{
    id: number;
    name: string;
    imgHeads: Array<number | null>;
    imgBodys: Array<number | null>;
    imgHands: Array<number | null>;
    imgFoots: Array<number | null>;
    weapon: number | null;
    shield: number | null;
  }>;
  equips: Array<{ id: number; name: string; img: number | null }>;
};

let generatedHumanRulesPromise: Promise<GeneratedHumanRules | null> | null = null;

/**
 * Load the baked character rules once per page. The same document (and URL) the loadout renderer
 * already fetches, so the browser serves it from the shared cache; it is requested separately only
 * because `loadHumanPartRules()` deliberately returns the part sheets alone.
 */
export function loadGeneratedHumanRules(): Promise<GeneratedHumanRules | null> {
  if (!generatedHumanRulesPromise) {
    generatedHumanRulesPromise = fetch("/character_sprites/character-rules.json")
      .then((response) => (response.ok ? (response.json() as Promise<GeneratedHumanRules>) : null))
      .catch(() => null);
  }
  return generatedHumanRulesPromise;
}

/**
 * The `character-rules.json` job row for a replay unit.
 *
 * The join is the canonical one, not a name guess: the app's own job name + rank resolves through
 * `COMBAT_JOB_ROWS_BY_NAME` (the recovered `KA GameData - Job.csv` combat rows, single owner
 * `battle-legality.ts`) to that row's `csvId`, and the baked document is keyed by exactly that id
 * (all 107 combat rows match). The literal-name spellings are kept only as a fallback for a payload
 * that already carries a sheet name.
 *
 * A job whose only native combat row is a different rank token than the payload declares (the single
 * `F Rank Scholar`, csvId 132) still resolves to that row - it is the only row the job has - while a
 * multi-rank job with no matching rank stays unresolved and is reported as such.
 */
function generatedJobRow(rules: GeneratedHumanRules, unit: GeneratedUnitView) {
  if (!unit.jobId) return null;
  const rows = COMBAT_JOB_ROWS_BY_NAME.get(unit.jobId);
  if (rows && rows.length > 0) {
    const rank = typeof unit.rank === "string" ? unit.rank : null;
    const row =
      (rank ? rows.find((entry) => entry.sheetRankToken === rank) : undefined) ??
      (rows.length === 1 ? rows[0] : undefined);
    if (row) {
      const byId = rules.jobs.find((job) => job.id === row.csvId);
      if (byId) return byId;
    }
  }
  const direct = rules.jobs.find((job) => job.name === unit.jobId);
  if (direct) return direct;
  if (!unit.rank) return null;
  for (const candidate of [`${unit.rank} Rank ${unit.jobId}`, `${unit.rank} Grade ${unit.jobId}`]) {
    const hit = rules.jobs.find((job) => job.name === candidate);
    if (hit) return hit;
  }
  return null;
}

/**
 * The 17-slot `HumanComponent.imgIds` array rebuilt from the recovered job record and equipment, for
 * a replay human whose builder payload carries no raw native array.
 *
 * The slot rules are the ones already documented in `battle-replay.ts` (HUMAN_IMAGE_SLOTS) and the
 * frozen demo characters in `human-battle-idle.json`: slot 1/2/4/10 come from the job's own
 * per-gender `imgBodys`/`imgFoots`/`imgHeads`/`imgHands`, slot 3 is the forced shoes skip, and slots
 * 11/12 are the equipped weapon's and shield's `EquipData.img` (written natively by
 * `ChangeWeaponImage`/`ChangeShieldImage`). Everything else stays -2 = "this configuration has no
 * image for the slot", so the shared resolver skips the line rather than substituting anything.
 *
 * The arrays in the baked document carry the source count as element 0, so the per-gender entry is
 * `gender + 1` - the same index the loadout renderer's own `variant` uses.
 *
 * Null when the job cannot be resolved or the job's own body/foot/head/hand images are absent: the
 * caller keeps its explicit fallback instead of drawing a guessed character.
 */
export function generatedHumanCharacterFromRules(
  unit: GeneratedUnitView,
  rules: GeneratedHumanRules,
): HumanIdleCharacter | null {
  if (!unit.human) return null;
  const job = generatedJobRow(rules, unit);
  if (!job) return null;
  const variant = (unit.gender === 1 ? 1 : 0) + 1;
  const body = job.imgBodys?.[variant];
  const foot = job.imgFoots?.[variant];
  const head = job.imgHeads?.[variant];
  const hand = job.imgHands?.[variant];
  if (typeof body !== "number" || typeof foot !== "number" || typeof head !== "number" || typeof hand !== "number") {
    return null;
  }
  const equipmentImage = (id: number | null | undefined): number => {
    if (id === null || id === undefined) return -2;
    const row = rules.equips.find((equip) => equip.id === id);
    return typeof row?.img === "number" ? row.img : -2;
  };
  const imgIds = new Array<number>(17).fill(-2);
  imgIds[0] = 0;
  imgIds[1] = body;
  imgIds[2] = foot;
  imgIds[3] = -2;
  imgIds[4] = head;
  imgIds[10] = hand;
  imgIds[11] = equipmentImage(unit.weaponId || job.weapon);
  imgIds[12] = equipmentImage(unit.shieldId ?? null);
  return {
    id: `replay:${unit.unitId}`,
    label: unit.name,
    jobSourceId: job.id,
    genderIndex: unit.gender ?? 0,
    flag: 0,
    imgIds,
    expectedDraws: 0,
    expectedLines: [],
  };
}

/**
 * The real human renderer input.
 *
 * A captured save's own `appearanceInputs` (flag + imgIds) always wins. Otherwise the array is
 * rebuilt from the replay's declared job/rank/gender/weapon/shield through the recovered loadout
 * rules (`generatedHumanCharacterFromRules`), which is what a `/battle` fight carries: the builder
 * publishes job identity and equipment, never the raw native array. The demo
 * `humanIdleCharacterForUnit` job/rank lookup is still deliberately NOT used - a real team unit is
 * never drawn with the demonstration Guard/Archer equipment.
 *
 * Null when neither source resolves, so the caller shows its explicit labelled fallback.
 */
export function generatedHumanCharacter(
  unit: GeneratedUnitView,
  rules?: GeneratedHumanRules | null,
): HumanIdleCharacter | null {
  if (!unit.human) return null;
  const inputs = unit.visual?.appearanceInputs;
  const imgIds = inputs?.imgIds;
  if (inputs && Array.isArray(imgIds) && imgIds.length > 0) {
    return {
      id: `replay:${unit.unitId}`,
      label: unit.name,
      jobSourceId: 0,
      genderIndex: unit.gender ?? 0,
      flag: typeof inputs.flag === "number" ? inputs.flag : 0,
      imgIds: [...imgIds],
      expectedDraws: 0,
      expectedLines: [],
    };
  }
  return rules ? generatedHumanCharacterFromRules(unit, rules) : null;
}

/**
 * The recovered SEB line records for one human unit at one frame, using the shared clip resolvers:
 * the animation_request-selected attack/idle clip, or the recovered damage / knock-down reaction
 * while the state chain says so. Returns null when there is no recovered clip for the state, which
 * is the caller's signal to use the explicit fallback - nothing is fabricated here.
 */
export function generatedHumanLinesForFrame(
  unitFrame: GeneratedUnitFrame,
  direction: number,
  /**
   * The recovered attack clip this fighter's own weapon selects, used only while attacking and only
   * when the runner's `animation_request` named no decoded clip.
   */
  attackClipId: HumanClipId | null = null,
): HumanIdleLine[] | null {
  const reactionFrame = Math.max(0, unitFrame.clipFrame);
  // `ChangeAnimation` is only called once per lifecycle, so every frame below is the state-relative
  // timer wrapped at its clip's own `maxFrame` - the same rules the shared recovered lifecycle
  // (`humanAnimationState`) applies. Without the wrap a reaction frame runs past the clip's last key
  // and every line resolves to "no sprite", i.e. the fighter disappears.
  const settledDirection = (((direction + HUMAN_KNOCKDOWN.spinThrough) % 4) + 4) % 4;
  switch (unitFrame.visual) {
    case "damaging":
      // State 6 lasts HUMAN_DAMAGE_WINDOW.updates; `UpdateDamaging 0x1585F04` loops the 6-frame clip.
      return humanReactionLinesAt("damage", direction, reactionFrame % HUMAN_DAMAGE_WINDOW.maxFrame);
    case "knocking_down": {
      /*
       * State 7 is entered the update the damage state exits, so the first knock-down update carries
       * timer 1; `UpdateKnockingDown 0x1586954` spins the direction every update through
       * `spinThrough`, then holds the settled one.
       */
      const knockdownUpdate = reactionFrame + 1;
      if (knockdownUpdate < HUMAN_KNOCKDOWN.downFrom) {
        const spin = Math.min(knockdownUpdate, HUMAN_KNOCKDOWN.spinThrough);
        const spinDirection = (((direction + spin) % 4) + 4) % 4;
        const variant = humanReactionDirection("knockDownSit", spinDirection);
        return humanReactionLinesAt(
          "knockDownSit",
          spinDirection,
          (knockdownUpdate - 1) % Math.max(1, variant.maxFrame),
        );
      }
      return humanReactionLinesAt("knockDownDown", settledDirection, 0);
    }
    case "down":
      return humanReactionLinesAt("knockDownDown", settledDirection, 0);
    case "leaving": {
      /*
       * `EnterLeaving` makes no ChangeAnimation call and the projectile is fired with its animation
       * flag zero, so the one-frame down pose stays selected (frame 0) while `UpdateLeaving
       * 0x15870CC` adds 2 to the direction each update, alternating the down asset and its mirror.
       * The state-relative timer equals the lifecycle's `leavingFrame` because state 8 is entered
       * exactly at `leavingAt`.
       */
      const leavingFrame = Math.max(0, reactionFrame - HUMAN_KNOCKDOWN.leavingAt);
      const leavingDirection = (((settledDirection + 2 * leavingFrame) % 4) + 4) % 4;
      return humanReactionLinesAt("knockDownDown", leavingDirection, 0);
    }
    /*
     * The walk and the revival walk are the same recovered `equip_walk` clip (`EnterMoving
     * 0x1584194` -> behaviour 2 -> base 8, rate 2); only the lifecycle that requested it differs, so
     * both states resolve through the one per-line walk table.
     */
    case "walking":
    case "revival_moving": {
      const walk = humanWalkDirection(direction);
      return humanWalkLinesAt(direction, wrapClipFrame(reactionFrame, walk.maxFrame));
    }
    case "attacking": {
      // `EnterAttacking 0x15854C0` picks the clip from the weapon's own `EquipData.motion`, so the
      // caller-resolved attack clip wins over a requested clip that could not be decoded.
      const clipId = attackClipId ?? unitFrame.clipId;
      return clipId ? humanLineRecordsAt(HUMAN_CLIPS[clipId], unitFrame.clipFrame) : null;
    }
    case "waiting":
      return unitFrame.clipId ? humanLineRecordsAt(HUMAN_CLIPS[unitFrame.clipId], unitFrame.clipFrame) : HUMAN_IDLE_LINES;
    default:
      return null;
  }
}

/** `AnimationSystem.Update`'s own frame wrap, for the record lists that do not carry a clip object. */
function wrapClipFrame(frame: number, maxFrame: number): number {
  const loop = Math.max(1, Math.floor(maxFrame));
  return ((Math.floor(frame) % loop) + loop) % loop;
}

/**
 * Which rule produced a fighter's bar block.
 *
 *  * `native-front` - the recovered `FighterSystem.IsMostFront 0x15884f8` predicate
 *    (`isMostFrontUnit`): the fighter stands on its own team's front row. Unchanged, and still the
 *    only rule applied to the enemy side.
 *  * `ally-rear-deviation` - a rear-rank ALLY. The recovered predicate returns no block here, which
 *    is exactly the reported symptom ("rear ally HPMP dark/invisible"). The published product rule
 *    for this pass is that every allied bar stays readable and current, so a rear ally is drawn even
 *    though the narrow front-row reading of `IsMostFront` would not draw it. This is a labelled
 *    product decision, not a re-derivation of the native rule.
 */
export type GeneratedBarTier = "native-front" | "ally-rear-deviation";

/**
 * The per-fighter HP/MP gauge values for one fighter at one frame.
 *
 * Values are always the folded frame's own HP/MP against the runner's own param-10/11 maxima - the
 * source state data - never a dimming heuristic and never a re-derived rate. An ally's bar block is
 * drawn whether or not the front-row predicate holds (see `GeneratedBarTier`); an enemy keeps the
 * recovered gate verbatim. A fighter with no frame draws nothing.
 */
export function generatedFighterVitals(
  side: "ally" | "enemy",
  unitFrame: GeneratedUnitFrame | undefined,
  rowOffset: number,
): FighterVitals | null {
  if (!unitFrame) return null;
  if (unitFrame.maxHp === null || unitFrame.maxMp === null) return null;
  if (side === "enemy" && !isMostFrontUnit(side, unitFrame.cell[1], rowOffset)) return null;
  const tier = generatedFighterBarTier(side, unitFrame, rowOffset);
  return {
    hp: { current: unitFrame.hp, max: unitFrame.maxHp, source: barTierSource(tier) },
    mp: { current: unitFrame.mp, max: unitFrame.maxMp, source: barTierSource(tier) },
  };
}

/** The rule a fighter's bar block comes from, or null when that fighter draws none. */
export function generatedFighterBarTier(
  side: "ally" | "enemy",
  unitFrame: GeneratedUnitFrame | undefined,
  rowOffset: number,
): GeneratedBarTier | null {
  if (!unitFrame) return null;
  if (isMostFrontUnit(side, unitFrame.cell[1], rowOffset)) return "native-front";
  return side === "ally" ? "ally-rear-deviation" : null;
}

function barTierSource(tier: GeneratedBarTier | null): string {
  return tier === "ally-rear-deviation"
    ? "generated replay frame; rear-rank ally under the labelled all-allied-bar product rule"
    : "generated replay frame";
}

/** How a unit frame should be drawn on the shared stage. */
export type GeneratedStageMode = "human-clip" | "generic-avatar" | "monster";

export function generatedStageMode(
  unit: GeneratedUnitView,
  rules?: GeneratedHumanRules | null,
): GeneratedStageMode {
  if (!unit.human) return "monster";
  return generatedHumanCharacter(unit, rules) ? "human-clip" : "generic-avatar";
}

/** The status base name from the runner's own status events; never invented. */
function statusNameOf(event: BattleReplayEvent): string | null {
  const fields = event as Record<string, unknown>;
  const name = fields.status ?? fields.statusName ?? fields.effectName ?? fields.name ?? fields.effect ?? null;
  return typeof name === "string" || typeof name === "number" ? String(name) : null;
}

/** One status tag from the runner's own status events; never invented. */
function statusTagOf(event: BattleReplayEvent): string | null {
  const name = statusNameOf(event);
  if (name === null) return null;
  const fields = event as Record<string, unknown>;
  const stacks = typeof fields.stacks === "number" ? fields.stacks : typeof fields.count === "number" ? fields.count : null;
  return stacks === null ? name : `${name} x${stacks}`;
}

function isStatusRemoval(kind: string): boolean {
  const k = kind.toLowerCase();
  return k.includes("remove") || k.includes("expire") || k.includes("clear") || k.includes("cure") || k.includes("end");
}

/**
 * The authoritative replay clock: ONE FRAME PER TICK. The runner emits many seqs per tick, so a
 * per-seq clock would run the wrong amount of battle time; here every event is grouped by its own
 * `tick` and ordered by seq, empty ticks are still frames, and the final frame carries the runner's
 * own final state. The per-unit HP/MP/state/cell fold is the shared `foldBattleReplayTimeline` (one
 * authoritative fold), and the animation/status lifecycle is read from the runner's own events.
 */
export function buildGeneratedFrames(replay: BattleReplayResult): GeneratedFrame[] {
  const timeline = foldBattleReplayTimeline(replay);
  const events = (replay.events as BattleReplayEvent[]).slice().sort((a, b) => a.seq - b.seq);
  const byTick = new Map<number, BattleReplayEvent[]>();
  let minTick = 0;
  let maxTick = 0;
  for (const event of events) {
    const list = byTick.get(event.tick);
    if (list) list.push(event);
    else byTick.set(event.tick, [event]);
    minTick = Math.min(minTick, event.tick);
    maxTick = Math.max(maxTick, event.tick);
  }
  if (replay.finalState.windowed && typeof replay.finalState.windowStopTick === "number") {
    maxTick = Math.max(maxTick, replay.finalState.windowStopTick);
  }
  const ticks: number[] = [];
  for (let tick = minTick; tick <= maxTick; tick += 1) ticks.push(tick);

  const unitIds = Object.keys(timeline.units);
  const replayUnit = new Map(replay.units.map((unit) => [unit.unitId, unit]));
  const cursor: Record<string, number> = {};
  const previous: Record<string, GeneratedVisualState> = {};
  const stateTick: Record<string, number> = {};
  const lastClip: Record<string, { clipId: HumanClipId | null; behavior: number | null; startTick: number }> = {};
  const statuses: Record<string, Set<string>> = {};
  const flights: Record<string, GeneratedBodyFlight | null> = {};
  /** units the runner has revived and that have not left moving 2 since (revival walk vs movement) */
  const revived: Record<string, boolean> = {};
  for (const unitId of unitIds) {
    cursor[unitId] = 0;
    previous[unitId] = "waiting";
    stateTick[unitId] = 0;
    lastClip[unitId] = { clipId: null, behavior: null, startTick: 0 };
    statuses[unitId] = new Set();
    flights[unitId] = null;
    revived[unitId] = false;
  }

  const frames: GeneratedFrame[] = [];
  for (let index = 0; index < ticks.length; index += 1) {
    const tick = ticks[index];
    const applied = byTick.get(tick) ?? [];

    // Status fold: the runner's own status events only; removal kinds drop the tag again.
    for (const event of applied) {
      const unitId = event.targetUnitId;
      if (!unitId || !statuses[unitId]) continue;
      if (!event.kind.toLowerCase().includes("status")) continue;
      if (isStatusRemoval(event.kind)) {
        const name = statusNameOf(event);
        if (name) {
          for (const tag of [...statuses[unitId]]) {
            if (tag === name || tag.startsWith(`${name} x`)) statuses[unitId].delete(tag);
          }
        }
        continue;
      }
      const tag = statusTagOf(event);
      if (tag) statuses[unitId].add(tag);
    }

    const units: Record<string, GeneratedUnitFrame> = {};
    for (const unitId of unitIds) {
      const points = timeline.units[unitId];
      const before = points[cursor[unitId]];
      while (cursor[unitId] + 1 < points.length) {
        const next = points[cursor[unitId] + 1];
        // The synthetic closing point carries seq MAX_SAFE_INTEGER and lands on the final tick.
        const nextTick = next.seq === Number.MAX_SAFE_INTEGER ? maxTick : next.tick;
        if (nextTick > tick) break;
        cursor[unitId] += 1;
      }
      const point = points[cursor[unitId]];
      if (point.state !== before.state) stateTick[unitId] = tick;
      for (const event of applied) {
        if (event.kind === "revive" && event.targetUnitId === unitId) revived[unitId] = true;
      }
      // The revival walk ends with the moving state; until then the revive event keeps it selected.
      if (point.state !== 2) revived[unitId] = false;
      previous[unitId] = visualStateFor(point.state, previous[unitId], revived[unitId]);

      for (const event of applied) {
        if (event.kind !== "animation_request" || event.targetUnitId !== unitId) continue;
        const behavior = typeof event.behavior === "number" ? event.behavior : null;
        const clip = typeof event.clip === "number" ? event.clip : null;
        const sideOfUnit = replayUnit.get(unitId)?.side ?? "ally";
        const resolved = generatedClipForBase(clip, DIRECTION_INDEX[BATTLE_DIRECTIONS[sideOfUnit]]);
        if (resolved) {
          lastClip[unitId] = { clipId: resolved, behavior, startTick: tick };
        } else {
          // The runner asked for a SEB whose clip is not exported yet (a sword attack base 16, for
          // example). Keep the last resolved clip running so the fighter keeps animating; only the
          // requested behaviour is recorded, and nothing is substituted for the missing pose.
          lastClip[unitId] = { ...lastClip[unitId], behavior };
        }
      }
      for (const event of applied) {
        if (event.kind !== "body_flight" || event.targetUnitId !== unitId) continue;
        const fields = event as Record<string, unknown>;
        const start = asVector3(fields.start);
        const end = asVector3(fields.end);
        if (!start || !end) continue;
        flights[unitId] = {
          tick: event.tick,
          speed: typeof fields.speed === "number" ? fields.speed : 0,
          start,
          end,
        };
      }
      const clip = lastClip[unitId];
      const unit = replayUnit.get(unitId);
      const visual = previous[unitId];
      // The reaction clips run on their own state timer; the requested clip runs on its request tick.
      const reactionPhase =
        visual === "damaging" ||
        visual === "knocking_down" ||
        visual === "down" ||
        visual === "leaving" ||
        visual === "revival_moving";
      const clipFrame = reactionPhase
        ? Math.max(0, tick - stateTick[unitId])
        : clip.clipId
          ? humanClipFrame(HUMAN_CLIPS[clip.clipId], Math.max(0, tick - clip.startTick))
          : 0;

      units[unitId] = {
        hp: point.hp,
        mp: point.mp,
        maxHp: parameterMaximum(unit, "10"),
        maxMp: parameterMaximum(unit, "11"),
        state: point.state,
        stateName: point.stateName,
        visual,
        cell: [point.cell[0], point.cell[1]],
        dead: point.hp <= 0,
        leaving: point.state === 8,
        status: [...statuses[unitId]].sort(),
        clipId: clip.clipId,
        clipFrame,
        clipBehavior: clip.behavior,
        stateTick: stateTick[unitId],
        flight: flights[unitId],
      };
    }
    frames.push({ index, tick, seq: applied[0]?.seq ?? -1, units, events: applied });
  }
  return frames;
}

export function generatedCoverage(
  replay: BattleReplayResult,
  units: GeneratedUnitView[],
  warnings: string[],
): GeneratedCoverage {
  const undecoded = units
    .filter((unit) => unit.weaponMotion !== null && RUNNER_WEAPON_MOTIONS.has(unit.weaponMotion) && !DECODED_WEAPON_MOTIONS.has(unit.weaponMotion))
    .map((unit) => unit.weaponMotion as number);
  const missingMonsterArt = units.filter((unit) => !unit.human).map((unit) => unit.name);
  const partialHumanAppearance = units
    .filter((unit) => unit.human && unit.coverageNotes.some((note) => note.includes("appearance")))
    .map((unit) => unit.name);
  const skillAnimationPending = (replay.events as BattleReplayEvent[]).filter(
    (event) => typeof event.skillId === "number",
  ).length;
  return {
    undecodedWeaponClips: [...new Set(undecoded)],
    missingMonsterArt,
    partialHumanAppearance,
    arenaPartial: true,
    skillAnimationPending,
    warnings: [...warnings],
    enemyArtPartial: missingMonsterArt.length > 0,
  };
}

/** The ordinary-attack clip decision for one unit: locomotion vs weapon behaviour. */
export function generatedAttackClip(unit: GeneratedUnitView): number | null {
  if (!unit.human || unit.weaponMotion === null) return null;
  if (!DECODED_WEAPON_MOTIONS.has(unit.weaponMotion)) return null;
  const base: Record<number, number> = ATTACK_BASE_BY_WEAPON_MOTION;
  return base[unit.weaponMotion] ?? null;
}

export type GeneratedSkillEvent = {
  seq: number;
  tick: number;
  actorUnitId: string | null;
  targetUnitId: string | null;
  skillId: number;
  skillSlot: number | null;
  motion: number | null;
  invocationLevel: number | null;
};

/** Skill metadata the replay already carries - the input contract for the skill-animation pass. */
export function generatedSkillEvents(replay: BattleReplayResult): GeneratedSkillEvent[] {
  const events = replay.events as BattleReplayEvent[];
  return events
    .filter((event) => typeof event.skillId === "number")
    .map((event) => {
      const catalogue = replay.catalog.skills[String(event.skillId)];
      return {
        seq: event.seq,
        tick: event.tick,
        actorUnitId: (event.casterUnitId ?? event.attackerUnitId ?? null) as string | null,
        targetUnitId: (event.targetUnitId ?? null) as string | null,
        skillId: event.skillId as number,
        skillSlot: typeof event.skillSlot === "number" ? event.skillSlot : null,
        motion: catalogue?.motion ?? null,
        invocationLevel: typeof event.level === "number" ? (event.level as number) : null,
      };
    });
}

/**
 * Coarse grouping of the runner's own event kinds, so the mounted generated replay can explain
 * damage / healing / status / death / reward AND the recovered action clips (animation_request,
 * projectile, body flight) in words. The classifier only reads the kind string the runner emitted;
 * it never infers an outcome.
 */
export function generatedEventGroup(
  kind: string,
): "damage" | "heal" | "status" | "death" | "prize" | "action" | "item" | "other" {
  const k = kind.toLowerCase();
  if (k.includes("prize") || k.includes("reward") || k.includes("drop") || k.includes("loot") || k.includes("chest")) return "prize";
  // The engine emits an explicit `battle_item` record per dispatched consumable use (tick, phase,
  // item, targets, remaining), so it belongs on the same timeline as attacks and states.
  if (k.includes("item")) return "item";
  if (k.includes("heal") || k.includes("recover")) return "heal";
  if (k.includes("death") || k.includes("dead") || k.includes("defeat") || k.includes("down") || k === "ko") return "death";
  if (k.includes("attack") || k.includes("damage") || k.includes("critical") || k.includes("weak") || k.includes("hit")) return "damage";
  if (
    k.includes("animation") ||
    k.includes("projectile") ||
    k.includes("body_") ||
    k.includes("effect") ||
    k.includes("invocation") ||
    k.includes("enqueue") ||
    k.includes("release")
  ) return "action";
  if (k.includes("state") || k.includes("status") || k.includes("cell") || k.includes("move") || k.includes("mp") || k.includes("skill")) return "status";
  return "other";
}

/**
 * One authoritative event read back in words. Every printed field is the runner's own; a field this
 * view does not know is printed raw instead of being interpreted, and nothing is recomputed.
 */
export function explainGeneratedEvent(
  event: BattleReplayEvent,
  nameOf: (unitId?: string | null) => string,
): string {
  const fields = event as Record<string, unknown>;
  const actor = nameOf(
    (event.attackerUnitId ?? event.casterUnitId ?? event.actorUnitId ?? event.ownerUnitId) as
      | string
      | null
      | undefined,
  );
  const target = nameOf(event.targetUnitId);
  const numbers = ["amount", "damage", "value", "hp", "hpAfter", "mp", "mpCost", "before", "after", "max"]
    .filter((key) => typeof fields[key] === "number")
    .map((key) => `${key} ${String(fields[key])}`)
    .join(", ");
  const skill = typeof event.skillId === "number" ? ` - skill ${event.skillId}` : "";
  const state = typeof event.stateName === "string" ? ` - state ${event.stateName}` : "";
  const behavior = typeof fields.behavior === "number" ? ` - behaviour ${String(fields.behavior)}` : "";
  const clip = typeof fields.clip === "number" ? ` - clip base ${String(fields.clip)}` : "";
  const cell = Array.isArray(fields.cell) ? ` - cell [${(fields.cell as number[]).join(",")}]` : "";
  const item = typeof fields.item === "string" ? ` - item ${fields.item}` : "";
  const itemParameter = typeof fields.parameter === "number" ? ` - parameter ${String(fields.parameter)}` : "";
  const itemRemaining = typeof fields.remaining === "number" ? ` - remaining ${String(fields.remaining)}` : "";
  const targetIds = Array.isArray(fields.targetUnitIds)
    ? (fields.targetUnitIds as unknown[])
    : Array.isArray(fields.targets)
      ? (fields.targets as unknown[])
      : null;
  const multiple = targetIds ? targetIds.length : null;
  const itemTargets =
    multiple !== null && multiple !== 1 ? ` - ${multiple} residents` : "";
  const head = event.targetUnitId ? `${actor} -> ${target}` : actor;
  return `tick ${event.tick} - seq ${event.seq} - ${event.kind}${skill}${state}${behavior}${clip}${cell}${item}${itemParameter}${itemTargets}${itemRemaining}: ${head}${numbers ? ` - ${numbers}` : ""}`;
}

export type GeneratedFinishOutcome = {
  /** "finish" when the payload carries the Python worker's finish block, else "finalState-only" */
  presence: "finish" | "finalState-only";
  verdict: number | null;
  battleState: number;
  ticks: number;
  stopReason: string;
  censored: boolean;
  prizeCallbacks: number;
  retainedRewards: unknown;
  /** Additive runner reward-entitlement report, or null when the payload predates it. */
  rewardEntitlement: BattleReplayRewardEntitlement | null;
  /** report.finish verbatim when present, otherwise null (older payloads) */
  finish: Record<string, unknown> | null;
  postFinish: Record<string, unknown> | null;
  /** the runner's own inventory-collection state, or "unknown" - never inferred */
  inventory: string;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * The finish / outcome / reward view. The Python evaluator worker adds the `finish` block; older
 * payloads do not carry it. This reads whatever is present verbatim (no recomputation) and marks the
 * inventory collection state unknown when the payload has nothing authoritative to say.
 */
export function generatedFinishOutcome(replay: BattleReplayResult): GeneratedFinishOutcome {
  const fields = replay as unknown as Record<string, unknown>;
  const finish = asRecord(fields.finish);
  const postFinish = asRecord(fields.postFinish);
  const inventoryCollection = finish ? asRecord(finish.inventoryCollection) : null;
  const inventoryState = inventoryCollection?.state;
  return {
    presence: finish ? "finish" : "finalState-only",
    verdict: replay.finalState.verdict,
    battleState: replay.finalState.battleState,
    ticks: replay.finalState.ticks,
    stopReason: replay.finalState.stopReason,
    censored: replay.finalState.censored,
    prizeCallbacks: replay.finalState.prizeCallbacks,
    retainedRewards: replay.finalState.retainedRewards,
    rewardEntitlement: replay.finalState.rewardEntitlement ?? null,
    finish,
    postFinish,
    inventory: typeof inventoryState === "string" ? inventoryState : "unknown (not reported by this payload)",
  };
}

/** The recovered clip base for a unit's ordinary attack, when the state chain says it is attacking. */
export function generatedFrameAttackBase(unit: GeneratedUnitView, unitFrame: GeneratedUnitFrame): number | null {
  if (unitFrame.visual !== "attacking") return null;
  if (unitFrame.clipId) return HUMAN_CLIPS[unitFrame.clipId].humanBase;
  return generatedAttackClip(unit);
}

/* ------------------------------------------------------------------ */
/* Shared scene placement (the one camera/viewport transform)          */
/* ------------------------------------------------------------------ */

/**
 * The fighter position in the shared logical scene, in scene pixels.
 *
 * This is the recovered native projection (`nativeViewPos`: cell -> world -> isometric -> minus the
 * battle camera), NOT the fitted percentage geometry of `stageLayout` - that fit centres the
 * formation in the 481-wide scene, which the mounted view window does not show, which is exactly why
 * a generated replay could read as an empty stage while 23 units were alive.
 *
 * While the runner's own `body_flight` is carrying the unit (knock-down / leaving), the unit is drawn
 * on that flight: the event's own world start/end at the recovered projectile step and parabola, so a
 * departing unit visibly leaves the field instead of standing on its last cell.
 */
export function generatedScenePlacement(input: {
  cell: [number, number];
  unitFrame: GeneratedUnitFrame | undefined;
  tick: number;
  rowOffset: number;
  /** presentation window pan over the scene, already applied to the backdrop by the caller */
  viewPanX: number;
}): { left: string; top: string } {
  const { cell, unitFrame, tick, rowOffset, viewPanX } = input;
  const flying =
    unitFrame?.flight &&
    (unitFrame.visual === "knocking_down" || unitFrame.visual === "down" || unitFrame.visual === "leaving");
  const pos = flying
    ? nativeWorldViewPos(...generatedFlightWorldAt(unitFrame.flight as GeneratedBodyFlight, tick), rowOffset)
    : nativeViewPos(cell[0], cell[1], rowOffset);
  return { left: `${pos.x - viewPanX}px`, top: `${pos.y}px` };
}

/**
 * The kind-10 self-projectile position at one tick. `ProjectileSystem` integrates one `speed` step
 * per update before the draw and writes `start.y + Parabola(height, length, frame)` to Y, with
 * `length = trunc(distance / speed)`; the height and the parabola are the recovered native values,
 * while start/end/speed are the runner's own event fields.
 */
function generatedFlightWorldAt(flight: GeneratedBodyFlight, tick: number): [number, number, number] {
  const [startX, startY, startZ] = flight.start;
  const [endX, , endZ] = flight.end;
  const dx = endX - startX;
  const dz = endZ - startZ;
  const distance = Math.hypot(dx, dz);
  if (distance <= 0) return [endX, startY, endZ];
  const step = Math.max(1, Math.trunc(flight.speed));
  const updates = Math.max(1, Math.trunc(distance / step));
  const frame = Math.min(updates, Math.max(0, Math.trunc(tick - flight.tick)) + 1);
  const height = Math.min(
    HUMAN_LEAVING.maxHeight,
    Math.max(HUMAN_LEAVING.minHeight, Math.trunc((distance * distance) / 100)),
  );
  const along = Math.min(distance, step * frame);
  return [
    startX + (dx / distance) * along,
    startY + humanParabola(height, updates, frame),
    startZ + (dz / distance) * along,
  ];
}

/**
 * The presentation window pan over the shared logical scene.
 *
 * The mounted window exposes `logicalViewWidth` logical px of the 481x197 scene starting at scene X
 * 0 (the recorded 240-wide surface), while the recovered camera keeps the whole formation near the
 * scene centre. The pan slides the window so the formation's own authoritative cells stay inside it;
 * it is applied to the backdrop by the same value, so scenery and fighters keep one transform. The
 * camera is set once (native `BattleSystem.Start`), so the caller passes the initial cells and the
 * view never follows a departing unit off the field.
 */
export function generatedViewPanX(
  cells: [number, number][],
  rowOffset: number,
  logicalViewWidth: number,
): number {
  if (cells.length === 0) return 0;
  const xs = cells.map(([column, row]) => nativeViewPos(column, row, rowOffset).x);
  const centre = (Math.min(...xs) + Math.max(...xs)) / 2;
  return Math.trunc(centre - logicalViewWidth / 2);
}

/**
 * The monster SEB clip and its update count for one generated frame: the wait/attack group of the
 * unit's native direction, advanced from the tick its current state began. The clip changes restart
 * the count, which is the recovered `ChangeAnimation` / `AnimationSystem.Update` pair; the frame
 * itself is selected inside the SEB record (`Seb.GetSprite`), never here.
 */
export function generatedMonsterClip(
  unitFrame: GeneratedUnitFrame,
  tick: number,
  direction: MonsterDirectionId,
): { clip: DecodedClip; update: number } {
  const attacking = unitFrame.visual === "attacking";
  const clip = monsterClipForDirection(attacking ? "attack" : "wait", direction);
  const update = Math.max(0, tick - (attacking ? unitFrame.stateTick : 0));
  return { clip, update };
}

/* ------------------------------------------------------------------ */
/* Player-facing wording                                               */
/* ------------------------------------------------------------------ */

/** The runner's verdict in words (`combat_ending.enter_ending`: 1 opponent, 2 own team, else open). */
export function generatedVerdictWord(verdict: number | null): string {
  if (verdict === 1) return "Victory";
  if (verdict === 2) return "Defeat";
  return "Unresolved";
}

export type GeneratedRewardSummary = {
  battleResult: string;
  pendingChests: number;
  rewardEntitlement: string;
  victoryRequired: string;
  /** Plain-language explanation for an unknown entitlement; never contains native offsets. */
  note: string | null;
};

/**
 * Player-facing pending-vs-entitlement summary for the outcome card.
 *
 * Reads only the runner's own reward-entitlement fields (nothing is recomputed from the timeline) and
 * keeps the battle result separate from the reward timing, so an unresolved or unknown reward count
 * never hides the result. Falls back to the queued chest count for older payloads.
 */
export function generatedRewardSummary(outcome: GeneratedFinishOutcome): GeneratedRewardSummary {
  const entitlement = outcome.rewardEntitlement;
  if (!entitlement) {
    return {
      battleResult: generatedVerdictWord(outcome.verdict),
      pendingChests: outcome.prizeCallbacks,
      rewardEntitlement: "unknown (older payload)",
      victoryRequired: "unknown",
      note: "This replay predates the reward-entitlement report; only the queued chest count is available.",
    };
  }
  let rewardEntitlement: string;
  let note: string | null = null;
  const awarded = typeof entitlement.awardedChestCount === "number"
    && Number.isSafeInteger(entitlement.awardedChestCount)
    && entitlement.awardedChestCount >= 0
    ? entitlement.awardedChestCount
    : null;
  if (awarded !== null) {
    rewardEntitlement = String(awarded);
  } else if (entitlement.battleVerdict === null) {
    rewardEntitlement = "unknown (battle unresolved)";
    note = "The battle did not resolve, so no reward entitlement can be certified.";
  } else {
    rewardEntitlement = "unknown (no pre-verdict certificate)";
    note = "A win is certified only when the pending chests were seen unchanged before the battle ended; "
      + "that certificate did not hold, so the awarded count stays unknown.";
  }
  return {
    battleResult: generatedVerdictWord(entitlement.battleVerdict),
    pendingChests: entitlement.pendingChestCount,
    rewardEntitlement,
    victoryRequired: entitlement.victoryRequired ? "yes" : "no",
    note,
  };
}

/**
 * One plain-language line per event a player can see at a tick, built only from the runner's own
 * fields. Internal bookkeeping (rng draws, animation requests, projectile bookkeeping, cell
 * bookkeeping) is counted instead of spelled out; the raw listing stays in the diagnostics panel.
 */
export function generatedTickSummary(
  events: BattleReplayEvent[],
  nameOf: (unitId?: string | null) => string,
): { lines: string[]; internal: number } {
  const lines: string[] = [];
  let internal = 0;
  for (const event of events) {
    const fields = event as Record<string, unknown>;
    const kind = event.kind.toLowerCase();
    const target = nameOf(event.targetUnitId);
    const actor = nameOf(
      (event.attackerUnitId ?? event.casterUnitId ?? event.actorUnitId) as string | null | undefined,
    );
    const damage =
      typeof fields.damage === "number" ? fields.damage : typeof fields.amount === "number" ? fields.amount : null;
    if (kind.includes("attack") && event.targetUnitId) {
      lines.push(
        `${actor} hits ${target}${damage === null ? "" : ` for ${damage}`}${fields.hpAfter === 0 ? " - defeated" : ""}`,
      );
    } else if (kind.includes("heal")) {
      lines.push(damage === null ? `${actor} heals ${target}` : `${actor} restores ${damage} HP to ${target}`);
    } else if (kind.includes("prize")) {
      lines.push(`${target} receives a reward`);
    } else if (kind === "state" && typeof fields.stateName === "string") {
      lines.push(`${target} is ${fields.stateName}`);
    } else if (kind === "mp") {
      lines.push(damage === null ? `${actor} spends MP` : `${actor} spends ${damage} MP`);
    } else if (kind.includes("item")) {
      /* The runner's own battle_item attempt/use record; Holy Herb dispatches this too. */
      const itemName = typeof fields.item === "string" ? fields.item : "item";
      const left = typeof fields.remaining === "number" ? ` (${String(fields.remaining)} left)` : "";
      const blocked = typeof fields.blocked === "string" ? ` - ${fields.blocked}` : "";
      lines.push(`${itemName} item ${fields.used === true ? "used" : "no effect"}${left}${blocked}`);
    } else if (kind === "resource_change") {
      /* The runner's own per-parameter delta: a restoration, never a spend. */
      const parameter = typeof fields.parameter === "number" ? (fields.parameter as number) : null;
      const before = typeof fields.before === "number" ? (fields.before as number) : null;
      const after = typeof fields.after === "number" ? (fields.after as number) : null;
      const delta = before !== null && after !== null ? after - before : null;
      if (parameter === 10) lines.push(delta === null ? `${target} restores HP` : `${target} restores ${delta} HP`);
      else if (parameter === 11) lines.push(delta === null ? `${target} restores MP` : `${target} restores ${delta} MP`);
      else internal += 1;
    } else if (kind.includes("body_flight")) {
      lines.push(`${target} is launched off the field`);
    } else {
      internal += 1;
    }
  }
  return { lines, internal };
}
