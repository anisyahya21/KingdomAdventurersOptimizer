import { useEffect, useMemo, useRef, useState, type ReactNode, type Ref } from "react";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import {
  getCharacterPreviewEnvelope,
  renderCharacterPreview,
  type CharacterRenderParams,
} from "@/lib/character-renderer";
import { HumanIdleLineLayer, NativeHumanBody } from "@/components/native-human-body";
import { NativeFighterGauges } from "@/components/native-fighter-gauges";
import { type FighterVitals } from "@/lib/native-fighter-gauges";
import {
  BATTLE_BACKDROP,
  COMBAT_TEXT_ROWS,
  COMBAT_TEXT_SHEET,
  COMBAT_TEXT_SIZE,
  MONSTER_LINES,
  NUMBER_GLYPH,
  NUMBER_ROW_IMAGES,
  NUMBER_ROW_Y,
  nativeInitialFormation,
  nativeScreenPos,
  monsterSpriteById,
  sebSpriteAt,
  type AnimationStateId,
  type BattleEncounter,
  type BattleUnit,
  type BeatState,
  type CombatTextLabel,
  type DecodedClip,
  type NumberStyle,
  type Slot,
} from "@/lib/battle-replay";
import type { HumanIdleCharacter, HumanIdleDraw, HumanIdleLine } from "@/lib/human-battle-idle";
import battleAnimationData from "@/game-data/battle-animation.json";
import type { HumanLeavingFlight, HumanRevivalVisual } from "@/lib/human-battle-animation";
import {
  HUMAN_RUNTIME_LINES,
  MONSTER_DATA_ENCOUNTERS,
  MONSTER_SEB_FAMILIES,
  bottomCentreOffset,
  buildHumanLines,
  monsterCellAtSebRect,
  monsterSheetFor,
  monsterFamily,
  shadowSheetFor,
} from "@/lib/native-body";

/**
 * Shared recovered battlefield stage - PASS 16 COMMAND 16.12.
 *
 * This module holds the stage geometry that BOTH replay modes must use: the logical scene basis,
 * the arena backdrop reference, the legacy comparison layout and the recovered native placement
 * (`nativeSlots` cells → `nativeScreenPos` projection → one uniform fit). The code was moved
 * verbatim out of `pages/battle-replay.tsx`; no formula, loop or ordering was changed, and the
 * reference replay now imports it from here.
 *
 * Still to move in later steps (audited in COMMAND 16.11): `UnitSprite` and its leaf renderers
 * (`HumanComposite`, `MonsterLineSprite`, `NativeMonsterBody`), `animationFor`, `humanLinesFor`, the
 * sprite-queue wiring and the `UnitSprite` prop mapping. Until those move, the sprite drawing stays
 * in the reference page and the generated surface stays data-only.
 */

/** Previous hand-placed spread layout, kept only so the change can be compared. */
export function legacyPosition(side: "ally" | "enemy", column: number, depth: number) {
  const vertical = 14 + column * 12;
  if (side === "enemy") {
    return { left: `${62 + depth * 8}%`, top: `${vertical}%` };
  }
  return { left: `${26 - depth * 8}%`, top: `${vertical}%` };
}

export const STAGE_ASPECT = { width: 480, height: 196 };
/** Fraction of the stage the formation is fitted into (uniform scale, presentation only). */
export const STAGE_FILL = 0.82;

/**
 * Logical battle scene (native basis). 481x197 is the recovered backdrop buffer / `battle/bg_100`
 * basis, kept as the CURRENT internal stage basis - it does not certify the unresolved native
 * camera-to-background mapping.
 *
 * Every scene quantity is expressed in these logical pixels (entity positions from `nativeViewPos`,
 * SEB line translations, SEB/OPT crop sizes, OPT destinations, overlays) and the whole scene is
 * scaled once by a single presentation transform. That is what keeps positions, sprite geometry and
 * sprite-local offsets on one transform: previously the positions were percentages of the stage
 * (so they scaled with it) while the sprites and their translations stayed at 1 CSS pixel.
 */
export const SCENE = { width: 481, height: 197 };
/**
 * bg_100 is 480x196 and is drawn 1:1 in logical coordinates at the recovered native origin
 * `backgroundX = trunc((logicalViewWidth - 480) / 2)`; the extra scene pixel is the buffer edge.
 */
export const SCENE_BACKGROUND = BATTLE_BACKDROP;

/**
 * Stage placement from recovered native geometry.
 *
 * 1. every unit gets its native cell from `nativeSlots` (CONFIRMED formulas),
 * 2. cells become screen offsets through the recovered isometric projection
 *    (`nativeScreenPos`: x = col*24 - row*24, y = -(col+row)*24/2),
 * 3. the whole formation is fitted with ONE uniform scale and centred.
 *
 * Step 3 is the only presentation choice; no per-unit coordinate is fitted.
 */
export type NativeFormation = ReturnType<typeof nativeInitialFormation>;

export function stageLayout(
  encounter: BattleEncounter,
  rosterCap: number | null,
  legacy: boolean,
  dynamic?: BeatState | null,
  formation?: NativeFormation,
) {
  /* one shared formation: the same offset for both teams, never one per team */
  const { allySlots, enemySlots } =
    formation ?? nativeInitialFormation(encounter.allies.length, encounter.enemies.length);
  const enemies =
    rosterCap === null ? encounter.enemies : encounter.enemies.slice(0, rosterCap);
  /** When dynamics are on, a unit is drawn on its current cell instead of its start slot. */
  const liveCell = (side: "ally" | "enemy", index: number): Slot | null => {
    if (!dynamic) return null;
    const unit = dynamic.units.find((entry) => entry.side === side && entry.index === index);
    if (!unit) return null;
    /*
     * A defeated unit keeps its cell in the native entity; only the formation helpers stop counting
     * it (HP < 1). The visual leaving flight starts from that same live cell, so the dead flag must
     * not make the renderer fall back to the original slot.
     */
    return unit.cell;
  };
  /** The KO visual flag is independent of the live cell: a defeated unit keeps its cell. */
  const isDead = (side: "ally" | "enemy", index: number) =>
    Boolean(dynamic?.units.some((unit) => unit.side === side && unit.index === index && !unit.alive));
  const enemySlotsLive = enemySlots.map((slot, index) => liveCell("enemy", index) ?? slot);
  const allySlotsLive = allySlots.map((slot, index) => liveCell("ally", index) ?? slot);

  if (legacy) {
    const allyDepthBase = Math.min(...allySlots.map((slot) => slot.row));
    const enemyDepthBase = Math.max(...enemySlots.map((slot) => slot.row));
    return {
      allies: encounter.allies.map((unit, index) => ({
        unit,
        index,
        slot: allySlots[index],
        cell: allySlotsLive[index],
        dead: dynamic ? isDead("ally", index) : false,
        ...legacyPosition("ally", allySlots[index].column, allySlots[index].row - allyDepthBase),
      })),
      enemies: enemies.map((unit, index) => ({
        unit,
        index,
        slot: enemySlots[index],
        cell: enemySlotsLive[index],
        dead: dynamic ? isDead("enemy", index) : false,
        ...legacyPosition("enemy", enemySlots[index].column, enemyDepthBase - enemySlots[index].row),
      })),
    };
  }

  const points = [
    ...allySlotsLive.map((slot) => nativeScreenPos(slot.column, slot.row)),
    ...enemySlotsLive.slice(0, enemies.length).map((slot) => nativeScreenPos(slot.column, slot.row)),
  ];
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  const scale = Math.min(
    (STAGE_ASPECT.width * STAGE_FILL) / spanX,
    (STAGE_ASPECT.height * STAGE_FILL) / spanY,
  );
  const centreX = (minX + maxX) / 2;
  const centreY = (minY + maxY) / 2;
  const place = (slot: { column: number; row: number }) => {
    const pos = nativeScreenPos(slot.column, slot.row);
    return {
      left: `${50 + ((pos.x - centreX) * scale * 100) / STAGE_ASPECT.width}%`,
      top: `${50 + ((pos.y - centreY) * scale * 100) / STAGE_ASPECT.height}%`,
    };
  };

  return {
    allies: encounter.allies.map((unit, index) => ({
      unit,
      index,
      slot: allySlots[index],
      cell: allySlotsLive[index],
      dead: dynamic ? isDead("ally", index) : false,
      ...place(allySlotsLive[index]),
    })),
    enemies: enemies.map((unit, index) => ({
      unit,
      index,
      slot: enemySlots[index],
      cell: enemySlotsLive[index],
      dead: dynamic ? isDead("enemy", index) : false,
      ...place(enemySlotsLive[index]),
    })),
  };
}

export type Placement = {
  unit: BattleUnit;
  index: number;
  slot: { column: number; row: number };
  cell?: { column: number; row: number };
  dead?: boolean;
  left: string;
  top: string;
};

type CompositeLayer = {
  layer: number;
  image: string;
  file: string;
  frames: { key: number; u: number; v: number; w: number; h: number; transX: number; transY: number; reversU: number; reversV: number }[];
};

const HUMAN_WAIT = (battleAnimationData as unknown as {
  humanCompositeWait?: { maxFrame: number; layers: CompositeLayer[] };
}).humanCompositeWait;

/**
 * Native human composite for equip_wait_up.seb (CONFIRMED data): the SEB's 14 layers
 * drawn in ascending order, each a w x h crop at (u,v) of its part sheet placed at
 * (transX, transY) relative to the fighter's bottom-centre anchor. Layers whose part
 * belongs to another resource group resolve to "?" and are skipped here.
 */
const HUMAN_LINES = buildHumanLines(
  HUMAN_WAIT as unknown as {
    layers: { layer: number; tex: number; image: string; frames: CompositeLayer["frames"] }[];
  },
);

export function HumanComposite({
  tick,
  showRuntimeParts,
}: {
  tick: number;
  showRuntimeParts: boolean;
}) {
  if (!HUMAN_WAIT) return null;
  const phase = ((tick % HUMAN_WAIT.maxFrame) + HUMAN_WAIT.maxFrame) % HUMAN_WAIT.maxFrame;
  return (
    <div className="relative" style={{ width: 0, height: 0 }}>
      {HUMAN_LINES
        .filter((line) => line.frames.length > 0)
        .map((line) => {
          let frame = line.frames[0];
          for (const candidate of line.frames) if (candidate.key <= phase) frame = candidate;
          if (!frame) return null;
          const runtime = line.asset === null;
          if (runtime && !showRuntimeParts) return null;
          const asset = line.asset ?? HUMAN_RUNTIME_LINES[line.line]?.asset;
          if (!asset) return null;
          // Runtime-supplied lines (texId -1) carry only a zero placeholder in the SEB; the
          // overlay keeps the 24x24 cell aligned with the face line. APPROX (the real values
          // come from the fighter's ImageComponent / job-dress tables).
          const u = runtime ? 0 : frame.u;
          const v = runtime ? 0 : frame.v;
          const w = runtime ? 24 : frame.w;
          const h = runtime ? 24 : frame.h;
          const transX = runtime ? -12 : frame.transX;
          const transY = runtime ? -32 : frame.transY;
          return (
            <span
              key={line.line}
              style={{
                position: "absolute",
                left: transX,
                top: transY,
                width: w,
                height: h,
                backgroundImage: `url(${asset})`,
                backgroundPosition: `-${u}px -${v}px`,
                imageRendering: "pixelated",
                transform: `scale(${frame.reversU ? -1 : 1}, ${frame.reversV ? -1 : 1})`,
              }}
            />
          );
        })}
    </div>
  );
}

/**
 * Native monster/pet body: two SEB lines (0 = shadow, 1 = body) drawn in ascending line
 * order, each anchored bottom-centre on the unit's cell. Sizes are the creature's own
 * pixels, which is why ordinary monsters and huge pets differ; the old universal
 * 40x30 / DrawScaledSeb scale-50 rule is not part of this path.
 *
 * Each line's crop comes from `Seb.GetSprite(frame, line)` (CONFIRMED, see
 * SEB_SPRITE_SELECTION_RULE): the shadow line and the body line have their own key lists, so they
 * are resolved independently and the selected record's own u/v/w/h picks the logical cell. No
 * animation-state -> pose mapping takes part any more.
 */
export type MonsterOptCell = NonNullable<ReturnType<typeof monsterCellAtSebRect>>;
export type MonsterLineRecord = NonNullable<ReturnType<typeof sebSpriteAt>>;

/**
 * Native OPT -> logical atlas reconstruction, one SEB line.
 *
 * logicalWidth/Height = cellW*cols x cellH*rows; each component copies ONLY
 * png[srcX, srcY, w, h] to logical[cellOriginX+destX, cellOriginY+destY, w, h] with no
 * scaling, i.e. drawImage(png, srcX, srcY, w, h, destX, destY, w, h) inside its cell.
 *
 * Implemented as a component-local w x h clipping viewport: the packed PNG is shifted by
 * (-srcX, -srcY) inside a w x h overflow:hidden box placed at (destX, destY). This is exactly
 * equivalent to the drawImage form and is required because these PNGs are TIGHTLY packed - a
 * single cell-sized clipping box would leak the neighbouring packed sprite.
 */
export function monsterCellLayer(cell: MonsterOptCell, flip: { u: boolean; v: boolean }) {
  const flipTransform =
    flip.u || flip.v
      ? `scale(${flip.u ? -1 : 1}, ${flip.v ? -1 : 1})`
      : undefined;
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        top: 0,
        width: cell.cellW,
        height: cell.cellH,
        transform: flipTransform,
      }}
    >
      <div
        style={{
          position: "absolute",
          left: cell.dest.x,
          top: cell.dest.y,
          width: cell.src.w,
          height: cell.src.h,
          overflow: "hidden",
        }}
      >
        <img
          src={cell.png}
          alt=""
          style={{
            position: "absolute",
            left: -cell.src.x,
            top: -cell.src.y,
            /**
             * The packed PNG must sit at its natural size: the crop is the viewport, not a fit.
             * Tailwind's preflight declares `img { max-width: 100%; height: auto }`, which squashed
             * every sheet whose packed width exceeds its component box (e.g. a 33px sheet inside a
             * 16px box), so the browser painted a resized sheet outside the viewport instead of the
             * 1:1 drawImage(srcX, srcY, w, h) crop. Measured in the OPT clip check:
             * ex_monster_m_01 painted 15.98x21.81 instead of 33x45 and showed no art at all.
             */
            width: "auto",
            height: "auto",
            maxWidth: "none",
            imageRendering: "pixelated",
          }}
        />
      </div>
    </div>
  );
}

/**
 * One SEB line of a native monster body, drawn at that line's own record translation. This is the
 * unit of the shared depth queue: shadow (line 0) and body (line 1) are separate entries.
 */
export function MonsterLineSprite({
  record,
  cell,
  mirror,
}: {
  record: MonsterLineRecord;
  cell: MonsterOptCell;
  mirror: { u: boolean; v: boolean };
}) {
  return (
    <div style={{ position: "absolute", left: record.transX, top: record.transY }}>
      {monsterCellLayer(cell, mirror)}
    </div>
  );
}

export function NativeMonsterBody({
  sprite,
  family,
  monsterId,
  clip,
  frame,
  mirror,
}: {
  sprite: { src: string; name: string; width: number; height: number };
  family: "small" | "xl";
  monsterId: number | null;
  /** native monster SEB clip chosen for this unit's state (wait / attack, up / right) */
  clip: DecodedClip;
  /** numeric SEB frame: the SEB record, not animState, decides which cell each line draws */
  frame: number;
  /**
   * Native mirror state of the selected SEB variant (the `,u`/`,v` load flags): the flip applies to
   * every line of the resource and is a draw-time flip about the crop rect centre, so it changes
   * neither the line origin nor the OPT destination.
   */
  mirror: { u: boolean; v: boolean };
}) {
  const data = monsterId === null ? undefined : MONSTER_DATA_ENCOUNTERS[monsterId];
  const bodySheet = data ? monsterSheetFor(data.img, data.size) : null;
  const shadowSheet = data ? shadowSheetFor(data.size) : null;
  const shadowRecord = sebSpriteAt(clip, frame, MONSTER_LINES.shadow);
  const bodyRecord = sebSpriteAt(clip, frame, MONSTER_LINES.body);
  const shadowCell =
    shadowSheet && shadowRecord ? monsterCellAtSebRect(shadowSheet, shadowRecord) : null;
  const bodyCell = bodySheet && bodyRecord ? monsterCellAtSebRect(bodySheet, bodyRecord) : null;
  if (shadowCell || bodyCell) {
    const renderCell = monsterCellLayer;
    return (
      <div className="relative" style={{ width: 0, height: 0 }}>
        {/*
          Native per-line placement: the crop of each SEB line is drawn at the entity origin plus
          that line's own record transX/transY (recovered equation: dest = entity screen position
          - camera + VIEW + SEB(transX,transY)). The OPT dest stays inside the crop, so the two
          coordinate systems are kept separate: entity -> SEB translation -> logical crop ->
          OPT component destination inside it. No half-size or bottom-centre compensation.
        */}
        {shadowCell && shadowRecord && (
          <div style={{ position: "absolute", left: shadowRecord.transX, top: shadowRecord.transY }}>
            {renderCell(shadowCell, mirror)}
          </div>
        )}
        {bodyCell && bodyRecord && (
          <div style={{ position: "absolute", left: bodyRecord.transX, top: bodyRecord.transY }}>
            {renderCell(bodyCell, mirror)}
          </div>
        )}
      </div>
    );
  }
  const spec = MONSTER_SEB_FAMILIES[family];
  const body = bottomCentreOffset(sprite.width, sprite.height);
  return (
    <div className="relative" style={{ width: 0, height: 0 }}>
      <img
        src={spec.shadow.asset}
        alt=""
        style={{
          position: "absolute",
          left: spec.shadow.transX,
          top: spec.shadow.transY,
          width: spec.shadow.w,
          height: spec.shadow.h,
          imageRendering: "pixelated",
        }}
      />
      <img
        src={sprite.src}
        alt={sprite.name}
        width={sprite.width}
        height={sprite.height}
        style={{
          position: "absolute",
          left: body.transX,
          top: body.transY,
          // The native origin container is zero-sized; preflight's max-width:100%
          // must not collapse the sprite to that origin's width.
          maxWidth: "none",
          imageRendering: "pixelated",
          // fallback path (no recovered OPT sheet for this monster id): same native mirror state
          transform: mirror.u ? "scaleX(-1)" : undefined,
        }}
      />
    </div>
  );
}
/**
 * One drawable SEB line of a battle unit, carried by the shared depth queue. Moved verbatim out of
 * `pages/battle-replay.tsx` (PASS 16 COMMAND 16.13) so the reference page and the generated stage
 * share one drawing contract.
 */
export type SpriteLinePayload =
  | {
      kind: "monster";
      placement: Placement;
      record: MonsterLineRecord;
      cell: MonsterOptCell;
      mirror: { u: boolean; v: boolean };
    }
  | {
      kind: "human";
      placement: Placement;
      characterId: string;
      draws: number;
      skipped: number;
      draw: HumanIdleDraw;
      /** PASS 15 COMMAND 15.2: the clip/frame this line was resolved at, for the DOM contract. */
      clipId: string | null;
      /** reaction clip id while damaged / knocked down, otherwise null */
      reactionId: string | null;
      seb: string;
      phase: string;
      frame: number;
      attackUpdate: number | null;
      reactionUpdate: number | null;
      knockdownUpdate: number | null;
      direction: number;
      flip: boolean;
      behaviour: number;
      /** the recovered `PositionComponent.offsetZ` arc while damaged */
      damageLiftZ: number;
      /** PASS 15 COMMAND 15.9: the live kind-10 flight while state 8 carries the unit */
      leaving: HumanLeavingFlight | null;
      /** PASS 15 COMMAND 15.11: the live state-2 revival walk, when active */
      revival: HumanRevivalVisual | null;
      /** revival-moving, complete, or unsupported-during-leaving */
      revivalState: string | null;
      /** the fixed teams[1] member count used by the native GetRowOffset() */
      rivalTeamMemberCount: number;
    };

/**
 * One unit's drawable box. Moved verbatim out of `pages/battle-replay.tsx` (PASS 16 COMMAND 16.13);
 * no prop, formula, DOM nesting, class name or React key changed.
 */
function dataAttributeProps(
  attributes: Record<string, string | number | null | undefined> | undefined,
): Record<string, string> {
  if (!attributes) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null) continue;
    out[`data-${key}`] = String(value);
  }
  return out;
}

export function UnitSprite({
  placement,
  side,
  showLabel,
  debugFormation,
  debugSeb,
  animState,
  animOffset,
  sebClip,
  sebFrame,
  sebMirror,
  sebTrace,
  nativeSize,
  anchorBottom,
  compositeTick,
  nativeBody,
  humanRuntimeParts,
  humanCharacter,
  humanLines,
  genericAvatar,
  dataAttributes,
  allyFacing = "up",
  fighterGauge,
  queueMode = false,
}: {
  placement: Placement;
  side: "ally" | "enemy";
  showLabel: boolean;
  debugFormation: boolean;
  /** debug overlay: the SEB record each body/shadow line resolves to for the current frame */
  debugSeb?: boolean;
  animState?: AnimationStateId;
  animOffset?: { x: number; y: number };
  /** native monster SEB clip + numeric frame for this unit (monsters only) */
  sebClip?: DecodedClip | null;
  sebFrame?: number;
  /** native mirror state of the unit's SEB variant (from the direction data, not from the side) */
  sebMirror?: { u: boolean; v: boolean };
  /** one labelled line per SEB line, for the debug overlay */
  sebTrace?: string[];
  nativeSize?: { width: number; height: number };
  /** native mode: the SEB draw anchors the sprite at the cell baseline (anchor 2, transY = -60). */
  anchorBottom?: boolean;
  /** native human composite tick (equip_wait_up); undefined = use the placeholder renderer */
  compositeTick?: number;
  /** native body path: monsters draw their real SEB lines instead of the fitted sprite */
  nativeBody?: boolean;
  /** draw the five runtime-supplied human lines from their chara sheets (APPROX placement) */
  humanRuntimeParts?: boolean;
  /**
   * Frozen native idle for this human ally (EQUIP_WAIT / TYPE_NORMAL row 0 / UP / frame 0). When
   * present it is the human renderer: one shared path for every ally that has a frozen
   * configuration, anchored on the entity origin. Null/undefined keeps the older human renderers.
   */
  humanCharacter?: HumanIdleCharacter | null;
  /**
   * PASS 15 COMMAND 15.2: the human clip and SEB frame this unit is on. The non-queue (legacy /
   * lab) human body and the queue path must resolve the same clip+frame, so both read them here
   * instead of each deciding for itself.
   */
  humanLines?: HumanIdleLine[] | null;
  /**
   * Explicit honest fallback when a human unit has no recovered appearance: a labelled generic
   * marker is drawn instead of any job/equipment preview. Undefined keeps the old preview path, so
   * the reference replay is untouched. The value is the reason text (e.g. "appearance inputs
   * unavailable"), and the marker carries it verbatim in `data-generic-avatar`.
   */
  genericAvatar?: string | null;
  /** Extra `data-*` attributes for the generated-replay DOM contract; reference callers omit it. */
  dataAttributes?: Record<string, string | number | null | undefined>;
  /**
   * Native per-fighter HP/MP bars (PASS 13): present only for the most-front unit of each team, so
   * origin; nothing here is scaled or offset per character type.
   */
  fighterGauge?: FighterVitals | null;
  allyFacing?: "up" | "right";
  /**
   * PASS 14: this unit's sprite lines are painted by the shared per-line depth queue instead of this
   * per-unit box, so the box keeps only the label/debug/gauge layers. The DOM box must never decide
   * sprite paint order again.
   */
  queueMode?: boolean;
}) {
  const sprite = monsterSpriteById(placement.unit.monsterId);
  /** RETRACTED for the native path: the old universal fit (isBoss 2.2 / 1.7 over a 80x60 cell). */
  const scale = placement.unit.isBoss ? 2.2 : 1.7;
  const bob = placement.unit.monsterId === null && animState === "walk" ? -2 : 0; // TEMPORARY human walk stand-in
  /**
   * Native monster placement audit (see SEB-POSE-SELECTION.md): the previous monster position was
   *   cell screen position + translate(-50%, -100% of the flex content) + translate(animOffset)
   *   + (-40, -60) + OPT dest
   * where the content is the label/debug spans, so -100% moved the body up by the *label height*
   * and animOffset was a scaled stand-in for the SEB translation the record now applies itself.
   * With the native body path the SEB line records carry that translation, so the unit keeps no
   * translate of its own and the flex column is left-aligned (items-center would otherwise shift
   * the 0x0 sprite origin by half the label width). The frozen human idle follows the same rule
   * (its lines carry the SEB translation); the legacy sprite path keeps the old anchoring.
   */
  const sebPlacedMonster = Boolean(nativeBody && sprite);
  /**
   * Native scene anchoring. In the native scene every unit is drawn from its entity origin:
   * monsters from the SEB line records, humans from their own renderers (the composite's SEB line
   * translations / the preview's pose-reference envelope). The label and debug rows live in a
   * separate absolutely positioned layer, so toggling them or changing their text cannot move the
   * artwork. Legacy mode keeps the previous flex-column + percentage anchoring unchanged.
   */
  const sceneAnchored = Boolean(nativeBody);
  const spriteBodyNode =
    placement.unit.monsterId === null ? (
      humanCharacter ? (
        <NativeHumanBody character={humanCharacter} lines={humanLines ?? null} />
      ) : compositeTick !== undefined ? (
        <HumanComposite tick={compositeTick} showRuntimeParts={humanRuntimeParts ?? false} />
      ) : genericAvatar ? (
        <span
          className="flex h-10 w-10 items-center justify-center rounded-sm border border-dashed border-muted-foreground/60 bg-black/40 text-center text-[7px] leading-tight text-muted-foreground"
          data-generic-avatar={genericAvatar}
          title={genericAvatar}
        >
          generic avatar
        </span>
      ) : (
        <CharacterPreviewCanvas
          jobName={placement.unit.jobName ?? "Guard"}
          rank={placement.unit.rank ?? "D"}
          variant={1}
          equipState={side === "ally" ? allyFacing : "right"}
          scale={2}
          poseFrame={0}
          label={placement.unit.label ?? placement.unit.jobName ?? "ally"}
          className={sceneAnchored ? undefined : "h-14 w-auto"}
          logicalAnchoring={sceneAnchored}
        />
      )
    ) : sprite && nativeBody && sebClip && sebFrame !== undefined ? (
      <NativeMonsterBody
        sprite={sprite}
        family={monsterFamily(sprite)}
        monsterId={placement.unit.monsterId}
        clip={sebClip}
        frame={sebFrame}
        mirror={sebMirror ?? { u: false, v: false }}
      />
    ) : sprite ? (
      <img
        src={sprite.src}
        alt={sprite.name}
        width={nativeSize ? nativeSize.width : Math.round(sprite.width * scale)}
        height={nativeSize ? nativeSize.height : Math.round(sprite.height * scale)}
        style={{
          maxWidth: "none",
          imageRendering: "pixelated",
          /**
           * Fitted-sprite fallback for monster ids without recovered MonsterData (APPROX: the
           * offsets are the native sprite size, the drawn size is the legacy fit). The facing
           * decision is the same native one: the entity direction selects the SEB variant and its
           * `,u` mirror flag.
           */
          transform: sebMirror?.u ? "scaleX(-1)" : undefined,
        }}
      />
    ) : (
      <span className="text-[10px] text-muted-foreground">#{placement.unit.monsterId}</span>
    );
  /** queued units draw their sprite lines in the shared depth layer, so this box contributes none */
  const spriteNode = queueMode ? null : spriteBodyNode;
  const labelNodes = (
    <>
      {showLabel && (
        <span className="whitespace-nowrap rounded bg-black/70 px-1 text-[8px] leading-tight text-white">
          {placement.unit.isBoss ? "★ " : ""}
          {placement.unit.label ?? sprite?.name ?? "unit"}
          <span className="text-white/50">
            {" "}c{placement.slot.column}r{placement.slot.row}
          </span>
        </span>
      )}
      {debugFormation && (
        <span className="whitespace-nowrap rounded bg-cyan-950/80 px-1 text-[8px] leading-tight text-cyan-200">
          #{placement.index} slot c{placement.slot.column}r{placement.slot.row} · cell c
          {placement.cell?.column ?? placement.slot.column}r
          {placement.cell?.row ?? placement.slot.row}
          {placement.dead ? " · KO" : ""}
        </span>
      )}
      {debugFormation && animState && (
        <span className="whitespace-nowrap rounded bg-amber-950/80 px-1 text-[8px] leading-tight text-amber-200">
          anim {animState}
        </span>
      )}
      {debugSeb &&
        (sebTrace ?? []).map((line, index) => (
          <span
            key={index}
            className="whitespace-nowrap rounded bg-emerald-950/80 px-1 font-mono text-[8px] leading-tight text-emerald-200"
          >
            {line}
          </span>
        ))}
    </>
  );
  return (
    <div
      className={`absolute ${placement.dead && !humanCharacter ? "opacity-30" : ""}`}
      data-unit-root
      data-side={side}
      data-unit-index={placement.index}
      data-unit-sprite={queueMode ? "queued" : "unit"}
      {...dataAttributeProps(dataAttributes)}
      style={{
        left: placement.left,
        top: placement.top,
        transform: sceneAnchored || sebPlacedMonster
          ? undefined
          : `translate(-50%, ${anchorBottom ? "-100%" : "-50%"}) translate(${animOffset?.x ?? 0}px, ${(animOffset?.y ?? 0) + bob}px)`,
      }}
    >
      {sceneAnchored ? (
        <>
          <div className="pointer-events-none absolute left-0 top-0">
            {spriteNode}
            {fighterGauge && <NativeFighterGauges vitals={fighterGauge} />}
          </div>
          <div className="pointer-events-none absolute left-0 top-0 flex flex-col items-start gap-0.5">
            {labelNodes}
          </div>
        </>
      ) : (
        <div className={`flex flex-col gap-0.5 ${sebPlacedMonster ? "items-start" : "items-center"}`}>
          {spriteNode}
          {labelNodes}
        </div>
      )}
    </div>
  );
}

/**
 * The mounted battlefield surface shared by the reference replay and the generated replay
 * (PASS 16 COMMAND 16.13). It owns the presentation window and the logical scene transform that
 * both modes must use - same 481x197 basis, same one uniform presentation scale, same native
 * backdrop origin - and renders its caller's sprite/overlay layers as `children`. The reference
 * page's DOM is unchanged: all `data-*` attributes, the frame class and the transform come from the
 * same expressions as before, so the visual oracles keep matching.
 */
export function BattleStage({
  hostRef,
  viewProfileId,
  logicalViewWidth,
  sourceHeight,
  sceneScale,
  presentationScale,
  presentationRatio,
  backgroundX,
  arenaFile,
  rowOffset,
  leavingFixtures,
  revivalFixtures,
  children,
}: {
  hostRef?: Ref<HTMLDivElement>;
  viewProfileId: string;
  logicalViewWidth: number;
  sourceHeight: number;
  sceneScale: number;
  presentationScale: number;
  presentationRatio: number;
  backgroundX: number;
  /** the arena backdrop URL (recovered bg family), drawn 1:1 at the native origin */
  arenaFile: string;
  rowOffset: number;
  leavingFixtures?: unknown;
  revivalFixtures?: unknown;
  children?: ReactNode;
}) {
  const displayScale = sceneScale * presentationScale;
  const displayScaleY = displayScale * presentationRatio;
  const presentedHeight = sourceHeight * displayScaleY;
  return (
    <div
      ref={hostRef}
      data-view-window="profile"
      data-logical-view-width={logicalViewWidth}
      className="relative w-full overflow-hidden rounded-lg border"
      style={{ height: presentedHeight }}
    >
      <div
        className="absolute left-0 top-0"
        data-scene-width={SCENE.width}
        data-scene-height={SCENE.height}
        data-view-profile={viewProfileId}
        data-row-offset={rowOffset}
        data-human-leaving-fixtures={
          leavingFixtures ? JSON.stringify(leavingFixtures) : undefined
        }
        data-human-revival-fixtures={
          revivalFixtures ? JSON.stringify(revivalFixtures) : undefined
        }
        data-logical-view-width={logicalViewWidth}
        data-logical-source-height={sourceHeight}
        data-background-x={backgroundX}
        data-vertical-ratio={presentationRatio}
        style={{
          width: SCENE.width,
          height: SCENE.height,
          transform: `scale(${displayScale}, ${displayScaleY})`,
          transformOrigin: "top left",
          backgroundImage: `url(${arenaFile})`,
          backgroundSize: `${SCENE_BACKGROUND.width}px ${SCENE_BACKGROUND.height}px`,
          backgroundPosition: `${backgroundX}px 0px`,
          backgroundRepeat: "no-repeat",
          imageRendering: "pixelated",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/*
 * There is deliberately no fitted-percentage adapter here any more.
 *
 * The generated replay used to place its fighters through `stageLayout`'s fitted geometry, which
 * centres the formation inside the 481x197 scene. The mounted window exposes logical X 0..240 of
 * that scene, so every fighter landed outside the visible window and a 23-unit battle rendered as an
 * empty stage. Both surfaces now take their fighter positions from the recovered native projection
 * (`nativeViewPos` in `@/lib/battle-replay`, the same rule the reference replay's native mode draws)
 * and, when the window has to slide, from the generated `generatedViewPanX` /
 * `generatedScenePlacement` pair which moves scenery and fighters by one shared value.
 */

/* ------------------------------------------------------------------ */
/* Shared recovered overlays (PASS 16, product/visual pass)            */
/* ------------------------------------------------------------------ */

/*
 * `DamageNumber` and `CombatText` were moved here verbatim from `pages/battle-replay.tsx` so the
 * reference replay and the generated replay draw the SAME recovered number/status art. The reference
 * page now imports them from this module; its DOM is unchanged.
 */

/** Damage number drawn from the original com/num_m_02.png digit rows. */
export function DamageNumber({ amount, style }: { amount: number; style: NumberStyle }) {
  const digits = String(Math.abs(Math.round(amount))).split("");
  const rowY = NUMBER_ROW_Y[style];
  const sheet = NUMBER_ROW_IMAGES[style];
  return (
    /*
     * `data-damage-number` is the probe hook the browser regression check reads; the glyphs and the
     * recovered row are unchanged.
     */
    <span className="inline-flex items-center" style={{ imageRendering: "pixelated" }} data-damage-number={Math.abs(Math.round(amount))} data-damage-style={style}>
      {digits.map((digit, index) => {
        const value = Number(digit);
        return (
          <span
            key={`${digit}-${index}`}
            style={{
              width: NUMBER_GLYPH.width,
              height: NUMBER_GLYPH.height,
              backgroundImage: `url(${sheet})`,
              backgroundPosition: `-${value * NUMBER_GLYPH.width}px -${rowY}px`,
              imageRendering: "pixelated",
            }}
          />
        );
      })}
    </span>
  );
}

/** Status label drawn from the original attack_str sheet (7 fixed rows). */
export function CombatText({ label }: { label: CombatTextLabel }) {
  const row = COMBAT_TEXT_ROWS.find((entry) => entry.label === label);
  if (!row) return null;
  return (
    <span
      style={{
        width: COMBAT_TEXT_SIZE.width,
        height: COMBAT_TEXT_SIZE.height,
        backgroundImage: `url(${COMBAT_TEXT_SHEET.file})`,
        backgroundPosition: `0px -${row.y}px`,
        imageRendering: "pixelated",
      }}
    />
  );
}

/** The scene-space size of the SEB origin, in logical pixels, for one loadout-rendered sprite. */
export type LoadoutSpriteBox = { left: number; top: number; width: number; height: number };

/**
 * One equipped character sprite drawn with the SHARED loadout renderer
 * (`lib/character-renderer.ts` - the same code `CharacterPreviewCanvas` and the Loadout Builder use).
 *
 * This is the draw path for a replay human whose builder payload carries no raw native
 * `appearanceInputs.imgIds` (BUILDER-CONTRACT.md section 2: the app has no recovered
 * job/equipment -> 17-slot builder, so the builder publishes job/rank/gender/weapon/shield instead).
 * The renderer composes the equipped body/foot/head/hand plus the weapon and shield sprites from the
 * same `character_sprites/character-rules.json`, so the fighter is drawn in its own gear instead of
 * as a generic avatar - and the pose frame is the loadout preview's own static pose, NOT a native
 * battle animation this app has not recovered.
 *
 * Anchoring: the SEB origin is placed on the parent's own origin, exactly like `logicalAnchoring` on
 * `CharacterPreviewCanvas`, so the sprite sits on the unit's scene position. Nothing is drawn (and no
 * default-sized canvas is shown) until the envelope has loaded; if the renderer cannot resolve the
 * job, the component draws nothing and the caller keeps its labelled placeholder.
 */
export function LoadoutStageSprite({
  params,
  anchor,
  onRendered,
}: {
  params: CharacterRenderParams;
  /** the unit's scene position; the SEB origin is placed here */
  anchor: { left: string; top: string };
  /** reports the drawn box in scene pixels, or null when this sprite cannot be drawn */
  onRendered?: (box: LoadoutSpriteBox | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [box, setBox] = useState<LoadoutSpriteBox | null>(null);
  const [failed, setFailed] = useState(false);
  const renderKey = useMemo(() => JSON.stringify(params), [params]);
  const report = useRef(onRendered);
  report.current = onRendered;

  useEffect(() => {
    let cancelled = false;
    setBox(null);
    setFailed(false);
    const canvas = canvasRef.current;
    if (!canvas) return;
    (async () => {
      const envelope = await getCharacterPreviewEnvelope(params);
      if (cancelled) return;
      if (!envelope) {
        setFailed(true);
        report.current?.(null);
        return;
      }
      const ok = await renderCharacterPreview(canvas, params);
      if (cancelled) return;
      if (!ok) {
        setFailed(true);
        report.current?.(null);
        return;
      }
      const pose = envelope.poseReferences?.[0] ?? { x: 0, y: 0 };
      const next = {
        left: envelope.cropX - envelope.originX + pose.x,
        top: envelope.cropY - envelope.originY + pose.y,
        width: envelope.cropW,
        height: envelope.cropH,
      };
      setBox(next);
      report.current?.(next);
    })().catch(() => {
      if (cancelled) return;
      setFailed(true);
      report.current?.(null);
    });
    return () => {
      cancelled = true;
    };
  }, [renderKey]);

  if (failed) return null;
  return (
    <div className="absolute" style={{ left: anchor.left, top: anchor.top, width: 0, height: 0 }} data-loadout-sprite>
      <canvas
        ref={canvasRef}
        aria-label={params.jobName}
        role="img"
        className="ka-pixel-art"
        style={{
          imageRendering: "pixelated",
          position: "absolute",
          left: box?.left ?? 0,
          top: box?.top ?? 0,
          width: box?.width ?? 0,
          height: box?.height ?? 0,
          visibility: box ? undefined : "hidden",
        }}
      />
    </div>
  );
}
