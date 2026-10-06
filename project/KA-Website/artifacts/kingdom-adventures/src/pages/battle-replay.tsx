import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw, Swords } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import { HumanIdleLineLayer, NativeHumanBody, useHumanPartRules } from "@/components/native-human-body";
import { NativeFighterGauges } from "@/components/native-fighter-gauges";
import {
  isMostFrontUnit,
  replayFighterVitals,
  type FighterVitals,
} from "@/lib/native-fighter-gauges";
import {
  BODY_PLACEMENT_EQUATION,
  HUMAN_RUNTIME_LINES,
  MODIFY_ANIMATION,
  MONSTER_DATA_ENCOUNTERS,
  MONSTER_SEB_FAMILIES,
  VIEW_ORIGIN,
  bottomCentreOffset,
  buildHumanLines,
  monsterCellAtSebRect,
  monsterFamily,
  monsterSheetFor,
  shadowSheetFor,
} from "@/lib/native-body";
import { PageHeader } from "@/components/ka/page-header";
import { GeneratedBattleReplay } from "@/components/generated-battle-replay";
import {
  SCENE,
  SCENE_BACKGROUND,
  STAGE_ASPECT,
  STAGE_FILL,
  CombatText,
  DamageNumber,
  legacyPosition,
  stageLayout,
  HumanComposite,
  BattleStage,
  UnitSprite,
  MonsterLineSprite,
  NativeMonsterBody,
  monsterCellLayer,
  type SpriteLinePayload,
  type MonsterLineRecord,
  type MonsterOptCell,
  type Placement,
} from "@/components/battle-stage";
import battleAnimationData from "@/game-data/battle-animation.json";
import {
  HUMAN_IDLE_CHARACTERS,
  HUMAN_IDLE_GROUP,
  HUMAN_IDLE_RESOLUTION,
  HUMAN_IDLE_STATE,
  humanIdleResolution,
  humanIdleCharacterForUnit,
  type HumanIdleCharacter,
  type HumanIdleDraw,
  type HumanIdleLine,
} from "@/lib/human-battle-idle";
import {
  HUMAN_ATTACK_WINDOW,
  HUMAN_DAMAGE_WINDOW,
  HUMAN_CLIPS,
  HUMAN_IDLE_CLIP,
  HUMAN_LEAVING,
  HUMAN_KNOCKDOWN,
  humanAnimationState,
  humanLeavingDestination,
  humanLeavingRowOffset,
  humanLineRecordsAt,
  humanNativeVisual,
  humanReactionLinesAt,
  humanWalkLinesAt,
  type HumanClip,
  type HumanAnimationState,
  type HumanLeavingFlight,
  type HumanMoveTarget,
  type HumanNativeVisual,
  type HumanRevivalVisual,
  type HumanWorldPosition,
} from "@/lib/human-battle-animation";
import {
  buildBattleSpriteQueue,
  type BattleQueueCell,
  type BattleQueueItem,
} from "@/lib/battle-depth-queue";
import {
  ARENAS,
  ARENA_SELECTION_EVIDENCE,
  BATTLE_BACKDROP,
  BATTLE_ENCOUNTERS,
  BATTLE_LAYERS,
  COMBAT_TEXT_ROWS,
  COMBAT_TEXT_SHEET,
  COMBAT_TEXT_SIZE,
  DEFAULT_VIEW_PROFILE_ID,
  EFFECT_SPRITES,
  HUMAN_IMAGE_MODEL_EVIDENCE,
  HUMAN_IMAGE_INPUTS,
  HUMAN_IMAGE_PIPELINE,
  HUMAN_IMAGE_SLOTS,
  FORMATION_CONSTANTS,
  FORMATION_RULES,
  DYNAMIC_RULES,
  ANIMATION_STATE_RULES,
  BATTLE_REGISTRATION_EVIDENCE,
  BATTLE_VIEW,
  CELL_BASIS,
  nativeViewPos,
  nativeWorldViewPos,
  MONSTER_CLIPS,
  MONSTER_DIRECTION_VARIANTS,
  BATTLE_DIRECTIONS,
  monsterClipForDirection,
  MONSTER_LINES,
  SEB_SPRITE_SELECTION_RULE,
  NATIVE_ANIMATION,
  NATIVE_FRAME_MS,
  nativeAnimationFrame,
  clipFrameAt,
  sebSpriteAt,
  HUD_SPRITES,
  INDICATOR_SPRITES,
  NATIVE_EFFECT_SPECS,
  NUMBER_GLYPH,
  NUMBER_ROW_IMAGES,
  NUMBER_ROW_Y,
  SCREEN_MAPPING_EVIDENCE,
  SHARED_ARENA_ID,
  SKILL_BALLOON_FRAMES,
  SKILL_BALLOON_SHEET,
  VIEW_PROFILES,
  backgroundXForViewWidth,
  encounterById,
  monsterSpriteById,
  nativeInitialFormation,
  nativeQueueingPosition,
  nativeScreenPos,
  nativeSlots,
  simulateOccupancy,
  verticalPresentationRatio,
  viewProfileById,
  visibleBackgroundInterval,
  type BeatState,
  type AnimationStateId,
  type BattleEncounter,
  type BattleEvent,
  type BattleUnit,
  type DecodedClip,
  type EvidenceLevel,
  type NumberStyle,
  type Slot,
  type UnitDynamicState,
} from "@/lib/battle-replay";

/* ------------------------------------------------------------------ */
/* Small overlay primitives (shared by both encounter families)         */
/* ------------------------------------------------------------------ */

function EvidenceBadge({ level }: { level: EvidenceLevel }) {
  const variant = level === "CONFIRMED" ? "default" : level === "SUPPORTED" ? "secondary" : "outline";
  return (
    <Badge variant={variant} className="font-mono text-[10px] tracking-wide">
      {level}
    </Badge>
  );
}

/*
 * `DamageNumber` and `CombatText` now live in `@/components/battle-stage` (moved verbatim) so the
 * generated replay draws the same recovered number/status art this page does.
 */

/* ------------------------------------------------------------------ */
/* Stage geometry                                                      */
/* ------------------------------------------------------------------ */


/*
 * The stage geometry that used to live here (`legacyPosition`, `STAGE_ASPECT`, `STAGE_FILL`,
 * `SCENE`, `SCENE_BACKGROUND`) now comes from `@/components/battle-stage` - PASS 16 COMMAND 16.12.
 * The definitions were moved verbatim; this page imports them.
 */

/*
 * `stageLayout` and `Placement` were moved verbatim to `@/components/battle-stage` (PASS 16 COMMAND
 * 16.12) and are imported above; the recovered geometry now lives in one shared module so the
 * generated stage can consume it from COMMAND 16.13.
 */

/* ------------------------------------------------------------------ */
/* Overlay for one event                                               */
/* ------------------------------------------------------------------ */

/**
 * Initial toggle state can be set from the URL (?native=0&composite=0&runtime-parts=1) so the
 * same battle state can be captured with the native renderer on and off.
 */
function queryFlag(key: string, fallback: boolean) {
  if (typeof window === "undefined") return fallback;
  const raw = new URLSearchParams(window.location.search).get(key);
  return raw === null ? fallback : raw !== "0" && raw !== "false";
}

/**
 * Recovered Replay: the one configuration this page shows without any override.
 *
 *  * `wairo-tank` ("Take out the Wairo Tank!") is the only encounter whose whole roster has
 *    recovered battle art - every enemy id in it (116, 121, 122, 114, 106) is in the recovered
 *    monster set, while the three other encounters are dominated by id 119 / 135 / 139 / 111 / 109
 *    / 117 / 112, which have no recovered battle sheet and no recovered draw rule yet.
 *  * `bg_100` is the arena both supplied recordings show; the per-fight selection rule is UNKNOWN,
 *    so it is not a choice the viewer should offer while claiming to be faithful.
 *  * the `recording` view profile is the ~240-logical-wide window measured from the supplied iPad
 *    recording (the 481-wide profile is the raw internal buffer and is not what the battle shows).
 *  * the roster and every unit's cell come from the replay's own state model
 *    (`simulateOccupancy`), not from a visible-roster cap.
 */
const RECOVERED_REPLAY = {
  encounterId: "wairo-tank",
  encounterLabel: "Take out the Wairo Tank!",
  arenaId: SHARED_ARENA_ID,
  viewProfileId: DEFAULT_VIEW_PROFILE_ID,
} as const;

function EventOverlay({
  event,
  encounter,
  layout,
  showEffects,
  showArrows,
  showBalloon,
}: {
  event: BattleEvent;
  encounter: BattleEncounter;
  layout: ReturnType<typeof stageLayout>;
  showEffects: boolean;
  showArrows: boolean;
  showBalloon: boolean;
}) {
  const actorList = event.side === "ally" ? layout.allies : layout.enemies;
  const targetList = event.targetSide === "ally" ? layout.allies : layout.enemies;
  const actor = actorList[event.actor];
  const target = targetList[event.target];
  if (!actor || !target) return null;

  const numberStyle: NumberStyle =
    event.kind === "critical" ? "critical" : event.side === "ally" ? "ally" : "enemy";
  const textLabel =
    event.kind === "missed"
      ? "Missed"
      : event.kind === "guard"
        ? "Guard"
        : event.kind === "dodge"
          ? "Dodge"
          : event.kind === "weak"
            ? "Weak"
            : event.kind === "def-down"
              ? "DEF Down"
              : event.kind === "sleep"
                ? "Sleep"
                : event.kind === "critical"
                  ? "Critical Hit"
                  : null;

  /**
   * Unit positions are logical scene pixels in the native scene and percentages in the legacy
   * layout, so the midpoint keeps whichever unit the layout uses instead of assuming percent.
   */
  const unitOf = (value: string) => (value.trim().endsWith("%") ? "%" : "px");
  const arrowLeft = `${(parseFloat(actor.left) + parseFloat(target.left)) / 2}${unitOf(actor.left)}`;
  const arrowTop = `${(parseFloat(actor.top) + parseFloat(target.top)) / 2}${unitOf(actor.top)}`;
  const flipped = event.side === "enemy";

  return (
    <>
      {showArrows && (
        <img
          src={INDICATOR_SPRITES.arrow}
          alt=""
          className="absolute -translate-x-1/2 -translate-y-1/2 opacity-80"
          style={{
            left: arrowLeft,
            top: arrowTop,
            imageRendering: "pixelated",
            transform: `translate(-50%, -50%) ${flipped ? "scaleX(-1)" : ""}`,
          }}
        />
      )}

      {showEffects && (event.kind === "attack" || event.kind === "critical" || event.kind === "weak") && (
        <img
          src={EFFECT_SPRITES.impact.file}
          alt=""
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{
            left: target.left,
            top: target.top,
            imageRendering: "pixelated",
            transform: `translate(-50%, -50%) scale(${event.kind === "critical" ? 2.4 : 1.8})`,
          }}
        />
      )}

      {showEffects && (event.kind === "guard" || event.kind === "dodge") && (
        <img
          src={EFFECT_SPRITES.flash.file}
          alt=""
          className="absolute -translate-x-1/2 -translate-y-1/2 opacity-90"
          style={{
            left: target.left,
            top: target.top,
            width: 46,
            height: 46,
            imageRendering: "pixelated",
          }}
        />
      )}

      {showEffects && event.kind === "sleep" && (
        <img
          src={HUD_SPRITES.sleep}
          alt=""
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{ left: target.left, top: `calc(${target.top} - 26px)`, width: 14, height: 42 }}
        />
      )}

      {showEffects && event.kind === "skill" && (
        <img
          src={event.side === "ally" ? EFFECT_SPRITES.orb.file : EFFECT_SPRITES.sparkle.file}
          alt=""
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{ left: actor.left, top: actor.top, imageRendering: "pixelated", opacity: 0.95 }}
        />
      )}

      {(textLabel || event.amount !== undefined) && (
        <div
          className="absolute -translate-x-1/2 -translate-y-full"
          style={{ left: target.left, top: `calc(${target.top} - 18px)` }}
        >
          <div className="flex flex-col items-center gap-0.5" style={{ transform: "scale(1.8)", transformOrigin: "bottom center" }}>
            {textLabel && <CombatText label={textLabel} />}
            {event.amount !== undefined && event.kind !== "missed" && (
              <DamageNumber amount={event.amount} style={numberStyle} />
            )}
          </div>
        </div>
      )}

      {showBalloon && event.kind === "skill" && (
        <div
          className="absolute -translate-x-1/2"
          style={{ left: actor.left, top: `calc(${actor.top} - ${SKILL_BALLOON_SHEET.offsetY * -1}px - 46px)` }}
        >
          <span
            style={{
              display: "block",
              width: SKILL_BALLOON_SHEET.width,
              height: SKILL_BALLOON_SHEET.height,
              backgroundImage: `url(${SKILL_BALLOON_SHEET.file})`,
              backgroundPosition: `0px -${event.side === "ally" ? SKILL_BALLOON_FRAMES.ally : SKILL_BALLOON_FRAMES.enemy}px`,
              imageRendering: "pixelated",
              transform: "scale(1.4)",
            }}
          />
          <div className="mt-1 rounded bg-black/60 px-1 text-center text-[9px] text-white">
            skill {event.skillId}
            {event.skillName ? ` · ${event.skillName}` : ""}
          </div>
        </div>
      )}

      {event.kind === "heal" && event.amount !== undefined && (
        <div className="absolute -translate-x-1/2" style={{ left: target.left, top: `calc(${target.top} - 30px)` }}>
          <span style={{ display: "inline-block", transform: "scale(1.6)" }}>
            <DamageNumber amount={event.amount} style="ally" />
          </span>
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

/**
 * PASS 16 COMMAND 16.9: two explicit modes on one route.
 *
 *  * `?mode=generated` (or `#generated`) renders the battle produced by `/api/battle-run` from the
 *    session store - never a fixture.
 *  * everything else is the untouched Recovered Replay below.
 *
 * The reference page keeps every hook unconditional inside its own component, so switching modes
 * cannot change its behaviour or its regression captures.
 */
export default function BattleReplayPage() {
  const mode =
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("mode") ??
        (window.location.hash === "#generated" ? "generated" : null);
  if (mode === "generated") return <GeneratedBattleReplay />;
  return <ReferenceReplayPage />;
}

function ReferenceReplayPage() {
  /**
   * Two surfaces, one renderer.
   *
   *  * **Recovered Replay** (default): the single faithful configuration - the Wairo Tank roster
   *    (the only encounter whose every enemy has recovered battle art), the recorded ~240 window,
   *    bg_100, the frozen native human idle, the replay state model, and no override of any kind.
   *  * **Lab surface** (`?lab=1`, or the button in the header card): every experimental switch,
   *    comparison renderer and evidence table, exactly as the recovery passes left them.
   *
   * The locked values are applied through the derived constants below, so the stage code keeps one
   * set of names and cannot show a lab override while in Recovered Replay.
   */
  const [labMode, setLabMode] = useState(queryFlag("lab", false));
  const [labEncounterId, setEncounterId] = useState(BATTLE_ENCOUNTERS[0].id);
  const [labArenaId, setArenaId] = useState(SHARED_ARENA_ID);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [labFullRoster, setFullRoster] = useState(true);
  const [labShowHud, setShowHud] = useState(true);
  const [labShowBubble, setShowBubble] = useState(false);
  const [labShowEffects, setShowEffects] = useState(true);
  const [labShowArrows, setShowArrows] = useState(true);
  const [labShowBalloon, setShowBalloon] = useState(true);
  const [labShowUnitLabels, setShowUnitLabels] = useState(false);
  /**
   * Superseded comparison state, no longer part of the viewer UI (kept reachable through the URL so
   * the recorded check harness and old captures can still reproduce it):
   *   ?legacy-layout=1   the historical hand-placed spread layout (not the native framing)
   *   ?composite=0       the legacy human renderers (composite / preview canvas) instead of the
   *   ?human-idle=0      frozen native idle, which is what the stage draws by default
   *   ?runtime-parts=1   the APPROX overlay for the human SEB's runtime-supplied lines 7-11
   *   ?facing-right=1    the character-preview "right" entry (native allies face UP)
   */
  const labLegacyLayout = queryFlag("legacy-layout", false);
  const [labDynamics, setDynamics] = useState(false);
  const [labDebugFormation, setDebugFormation] = useState(false);
  const [labDebugGrid, setDebugGrid] = useState(false);
  /** debug overlay: which SEB record each monster line resolves to for the current frame */
  const [labDebugSeb, setDebugSeb] = useState(false);
  const [labNativeMode, setNativeMode] = useState(queryFlag("native", true));
  /**
   * Battle view profile: which slice of the logical battle space the surface window exposes.
   * Default = the recording-supported native view (~240 logical width); the 481-wide entry is the
   * previous presentation kept as a debug/comparison mode. Never a formation/camera input.
   */
  const [labViewProfileId, setViewProfileId] = useState(DEFAULT_VIEW_PROFILE_ID);
  /**
   * Legacy human renderers for the comparison mode behind ?human-idle=0: true = the incomplete
   * native-template SEB composite from battle-animation.json for ally 0, false = the website's own
   * character assembly (character-renderer.ts poses + slot selection) for ally 1, drawn at the
   * recovered bottom-centre battle anchor.
   */
  const nativeAlly = queryFlag("composite", true);
  /**
   * Frozen native human idle (default ON): EQUIP_WAIT / TYPE_NORMAL row 0 / direction UP / SEB
   * frame 0, resolved per line through the character's imgIds and the baked img.inf/OPT sheets.
   * One shared path for every ally that has a frozen configuration (the two demo characters in
   * src/game-data/human-battle-idle.json); the composite/preview toggles below stay available as
   * comparison renderers and are what a unit still uses when it has no frozen configuration.
   */
  const [labHumanIdle, setHumanIdle] = useState(queryFlag("human-idle", true));
  /**
   * Lines 7-11 (hair / hat / accessory / eye / mouth) carry texId -1 and a zero placeholder in
   * equip_wait_up.seb: the game fills them from the fighter's ImageComponent, whose values come
   * from the job/dress tables (World.CreateHuman 0x1476854 -> Entity.AddImage 0x146F2A4). Those
   * values are not recovered, so drawing them is a labelled APPROX overlay (cell aligned with
   * the face line). OFF by default.
   */
  const labHumanRuntimeParts = queryFlag("runtime-parts", false);
  /** UNKNOWN / temporary presentation parameter: the native dst rect (gfx +8/+12) is not recovered. */
  const [presentationScale, setPresentationScale] = useState(1);

  /*
   * Effective configuration. Recovered Replay pins every value to the recovered one; the lab
   * surface (?lab=1) exposes the experimental switches. Nothing below the stage can then show a
   * lab override, and the roster/profile/encounter can no longer be "played with".
   */
  const encounterId = labMode ? labEncounterId : RECOVERED_REPLAY.encounterId;
  const arenaId = labMode ? labArenaId : RECOVERED_REPLAY.arenaId;
  const viewProfileId = labMode ? labViewProfileId : RECOVERED_REPLAY.viewProfileId;
  const nativeMode = labMode ? labNativeMode : true;
  const humanIdle = labMode ? labHumanIdle : true;
  const fullRoster = labMode ? labFullRoster : true;
  const dynamics = labMode ? labDynamics : true;
  const showEffects = labMode ? labShowEffects : true;
  const showArrows = labMode ? labShowArrows : true;
  const showBalloon = labMode ? labShowBalloon : true;
  const showHud = labMode ? labShowHud : true;
  const showBubble = labMode ? labShowBubble : false;
  const showUnitLabels = labMode ? labShowUnitLabels : false;
  const debugFormation = labMode ? labDebugFormation : false;
  const debugGrid = labMode ? labDebugGrid : false;
  const debugSeb = labMode ? labDebugSeb : false;
  const legacyLayout = labMode ? labLegacyLayout : false;
  const humanRuntimeParts = labMode ? labHumanRuntimeParts : false;
  /**
   * Native battle direction for allies is UP(0) (`InitFighters` stores `Invert(slot direction)`).
   * The character-preview renderer labels its two base direction entries "up" and "right" after the
   * native `*_up.seb` / `*_right.seb` files, so native UP(0) maps to "up"; "right" is the
   * direction-1 entry and is only reachable through the lab surface's `?facing-right=1`.
   */
  const allyFacing: "up" | "right" = labMode && queryFlag("facing-right", false) ? "right" : "up";

  const encounter = useMemo(() => encounterById(encounterId), [encounterId]);
  const arena = ARENAS.find((entry) => entry.id === arenaId) ?? ARENAS[0];
  const beats = useMemo(() => simulateOccupancy(encounter), [encounter]);
  /**
   * One shared formation for the whole battle: the rival slot's offset (`GetRowOffset()` reads the
   * fixed rival team, never "the other team relative to the caller") plus both teams' start cells.
   * It is derived from the full rosters, so neither the visible roster cap nor the survivor count
   * can change it, and the same value feeds the camera/projection and both teams.
   */
  const formation = useMemo(
    () => nativeInitialFormation(encounter.allies.length, encounter.enemies.length),
    [encounter],
  );
  const rowOffset = formation.rowOffset;
  /**
   * PASS 15 COMMAND 15.10 test fixtures (lab surface only).
   *
   * These call the production leaving helper with asymmetric team sizes and both team sides, so the
   * verifier can compare the browser's own output against an independent re-derivation instead of
   * re-testing its own Python mirror. The native source is the fixed `teams[1]` slot, so the
   * `rivalTeamMemberCount` is the same for a team-0 and a team-1 fighter in one battle.
   */
  const leavingFixtures = labMode
    ? [
        {
          id: "A",
          allies: 2,
          enemies: 21,
          team: 0,
          cell: { column: 0, row: 6 },
          rivalTeamMemberCount: 21,
        },
        {
          id: "B",
          allies: 6,
          enemies: 5,
          team: 0,
          cell: { column: 0, row: 6 },
          rivalTeamMemberCount: 5,
        },
        {
          id: "C",
          allies: 2,
          enemies: 21,
          team: 1,
          cell: { column: 0, row: 5 },
          rivalTeamMemberCount: 21,
        },
      ].map((fixture) => ({
        ...fixture,
        rowOffset: humanLeavingRowOffset(fixture.rivalTeamMemberCount),
        end: humanLeavingDestination(fixture.cell, fixture.team, fixture.rivalTeamMemberCount),
      }))
    : null;
  /**
   * PASS 15 COMMAND 15.11 queue-position fixtures (lab surface only).
   *
   * The native `GetQueueingPosition` search is count-based; these synthetic formations exercise the
   * asymmetric team sizes and the team-1 branch without changing the replay's combat state.
   */
  const revivalFixtures = labMode
    ? (() => {
        const unit = (
          side: "ally" | "enemy",
          index: number,
          cell: { column: number; row: number },
        ): UnitDynamicState => ({
          index,
          side,
          original: cell,
          cell,
          alive: true,
          movedThisBeat: false,
        });
        const cases = [
          {
            id: "A",
            side: "ally" as const,
            targetIndex: 0,
            rowOffset: 5,
            units: [unit("ally", 1, { column: 1, row: 6 })],
          },
          {
            id: "B",
            side: "ally" as const,
            targetIndex: 0,
            rowOffset: 3,
            units: [
              unit("ally", 1, { column: 0, row: 4 }),
              unit("ally", 2, { column: 0, row: 5 }),
              unit("ally", 3, { column: 1, row: 4 }),
              unit("ally", 4, { column: 3, row: 4 }),
              unit("ally", 5, { column: 4, row: 4 }),
            ],
          },
          {
            id: "C",
            side: "enemy" as const,
            targetIndex: 0,
            rowOffset: 5,
            units: [
              unit("enemy", 1, { column: 0, row: 5 }),
              unit("enemy", 2, { column: 1, row: 5 }),
              unit("enemy", 3, { column: 4, row: 5 }),
            ],
          },
        ];
        return cases.map((entry) => ({
          ...entry,
          destination: nativeQueueingPosition({
            side: entry.side,
            targetIndex: entry.targetIndex,
            units: entry.units,
            rowOffset: entry.rowOffset,
          }),
        }));
      })()
    : null;
  /**
   * One presentation transform for the whole scene: measured from the wrapper's *content* box (the
   * border lives on the wrapper, so it cannot skew the horizontal scale) and applied to the fixed
   * 481x197 logical scene. `presentationScale` is the existing manual zoom and multiplies into it.
   *
   * The view profile sets the window width, so the surface window is exactly `logicalViewWidth`
   * logical px wide and taller/wider scene content is clipped naturally - the fighters keep their
   * nativeViewPos coordinates and are never moved inward. The vertical scale is derived from the
   * horizontal one through the native 196/192 presentation ratio, so background and fighters stay
   * inside one shared transform and the recovered anisotropy applies to both.
   */
  const sceneHostRef = useRef<HTMLDivElement>(null);
  const [sceneScale, setSceneScale] = useState(1);
  const viewProfile = viewProfileById(viewProfileId);
  const backgroundX = backgroundXForViewWidth(viewProfile.logicalViewWidth);
  const visibleBackground = visibleBackgroundInterval(viewProfile.logicalViewWidth);
  const presentationRatio = verticalPresentationRatio(viewProfile);
  useEffect(() => {
    const host = sceneHostRef.current;
    if (!host) return;
    const update = () => {
      const width = host.clientWidth;
      if (width > 0) setSceneScale(width / viewProfile.logicalViewWidth);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(host);
    return () => observer.disconnect();
  }, [nativeMode, viewProfile.logicalViewWidth]);
  /*
   * The scene transform and the presented band height now come from the shared mounted
   * `<BattleStage>` (PASS 16 COMMAND 16.13); this page supplies the same inputs it always did.
   */
  // Recovered native composition constants.
  const BACKDROP = SCENE;
  const MONSTER_FRAME = { width: 80, height: 60 }; // CONFIRMED decoded SEB frame rect
  const MONSTER_DRAW_SCALE = 50; // CONFIRMED w7=0x32 at the DrawScaledSeb call site
  const nativeMonsterSize = {
    width: Math.trunc((MONSTER_FRAME.width * MONSTER_DRAW_SCALE) / 100),
    height: Math.trunc((MONSTER_FRAME.height * MONSTER_DRAW_SCALE) / 100),
  };
  const beatState = beats[Math.min(step, beats.length - 1)];
  /**
   * Live state counts. Recovered Replay drives both the stage and the HUD from the replay's own
   * state, so the roster on screen and the HUD figures cannot disagree.
   */
  const liveCounts = {
    ally: beatState.units.filter((unit) => unit.side === "ally" && unit.alive).length,
    enemy: beatState.units.filter((unit) => unit.side === "enemy" && unit.alive).length,
  };
  const layout = useMemo(() => {
    const base = stageLayout(
      encounter,
      fullRoster ? null : 6,
      legacyLayout,
      dynamics ? beatState : null,
      formation,
    );
    if (!nativeMode) return base;
    // Recovered native mode: place every unit on its native view pixel, in logical scene pixels.
    const project = <T extends { cell?: { column: number; row: number }; slot: { column: number; row: number } }>(entry: T) => {
      const cell = entry.cell ?? entry.slot;
      const pos = nativeViewPos(cell.column, cell.row, rowOffset);
      return {
        ...entry,
        left: `${pos.x}px`,
        top: `${pos.y}px`,
      };
    };
    return { allies: base.allies.map(project), enemies: base.enemies.map(project) };
  }, [encounter, fullRoster, legacyLayout, dynamics, beatState, nativeMode, rowOffset, formation]);
  const event = encounter.timeline[Math.min(step, encounter.timeline.length - 1)];
  const bossName = monsterSpriteById(encounter.enemies[0]?.monsterId ?? null)?.name ?? "boss";

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setStep((current) => (current + 1) % encounter.timeline.length);
    }, 1100);
    return () => window.clearInterval(timer);
  }, [playing, encounter.timeline.length]);

  useEffect(() => {
    setStep(0);
  }, [encounterId]);

  /**
   * Native update clock: `AnimationSystem.Update` runs once per rendered frame and the game sets
   * `Application.targetFrameRate` to the form manager's 20 (main.Main.OnUpdate 0x169401C +
   * form.FormManager..ctor 0x16B8E6C), i.e. one animation update every 50 ms.
   */
  const [animTick, setAnimTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setAnimTick((value) => value + 1), NATIVE_FRAME_MS);
    return () => window.clearInterval(timer);
  }, []);

  /**
   * Per-unit animation phase, emulating `ChangeAnimation 0x165DEA8` (`SebComponent.frame = 0`):
   * a unit's frame restarts at 0 the update its SEB clip changes and advances one frame per update
   * afterwards. The map is a render-time cache of (clip id -> start tick); the same inputs always
   * produce the same entry, so re-renders do not change the result.
   */
  const unitAnimationStart = useRef(new Map<string, { clipId: string; startTick: number }>());

  /**
   * Human attack timing (PASS 15 COMMAND 15.2).
   *
   * The replay timeline carries `actor / target / kind / amount / ko` and no attack duration, so the
   * native window comes from the recovered fighter state machine instead: `UpdateAttacking
   * 0x1585568` resolves the attack at blackboard timer 11 and leaves the state at timer >= 20, and
   * `FighterSystem.Update 0x1583CF8` increments that timer once per rendered battle update. The
   * attack visual therefore starts on the update the unit's attack event becomes current and lasts
   * exactly `HUMAN_ATTACK_WINDOW.updates` (20) updates - a property of the state, never of the SEB
   * length. Leaving the attack event (any step whose actor/kind is not this unit attacking) clears
   * the entry, so re-entering the event plays it again from update 0.
   */
  const humanAttackStart = useRef(new Map<string, number>());
  /** the update each human ally's damage reaction started on (see humanDamageStartFor) */
  const humanDamageStart = useRef(new Map<string, number>());
  /**
   * PASS 15 COMMAND 15.9: a lethal hit's reaction start is persistent.
   *
   * The native damage -> knock-down -> down -> leaving chain spans many 1.1 s replay beats, so it
   * cannot be re-derived from whichever event happens to be current later. Once a KO hit is seen the
   * unit keeps its lethal start until Reset or an encounter change; revival is deliberately outside
   * this pass, so a later `revive` event does not cancel the visual yet.
   */
  const humanLethalStart = useRef(new Map<string, number>());
  /**
   * PASS 15 COMMAND 15.11: the persistent state-2 revival start. It records the update the cure
   * event became current, the visual start point, the recovered queueing target and the walk
   * direction selected by `EnterMoving`.
   */
  const humanRevivalStart = useRef(
    new Map<
      string,
      {
        tick: number;
        start: HumanWorldPosition;
        target: HumanMoveTarget;
        walkDirection: number;
        unsupported: boolean;
      }
    >(),
  );

  /** Reset every visual phase cache with the replay clock, so a replay starts from the idle state. */
  const resetReplay = () => {
    unitAnimationStart.current.clear();
    humanAttackStart.current.clear();
    humanDamageStart.current.clear();
    humanLethalStart.current.clear();
    humanRevivalStart.current.clear();
    setStep(0);
  };

  useEffect(() => {
    unitAnimationStart.current.clear();
    humanAttackStart.current.clear();
    humanDamageStart.current.clear();
    humanLethalStart.current.clear();
    humanRevivalStart.current.clear();
  }, [encounterId]);

  /**
   * The update the current attack event started on for one unit, registering it on first sight. The
   * reaction of the *target* needs this value, so it is resolved for the event's actor before the
   * queue is built rather than depending on iteration order.
   */
  const attackStartFor = (side: "ally" | "enemy", index: number): number | null => {
    const unitKey = `${side}-${index}`;
    const attacking =
      event.side === side &&
      event.actor === index &&
      (event.kind === "attack" || event.kind === "critical" || event.kind === "weak" || event.kind === "skill");
    if (!attacking) {
      humanAttackStart.current.delete(unitKey);
      return null;
    }
    let startTick = humanAttackStart.current.get(unitKey);
    if (startTick === undefined) {
      startTick = animTick;
      humanAttackStart.current.set(unitKey, startTick);
    }
    return startTick;
  };

  /** The persistent reaction start for a lethal hit, registered on the KO event. */
  const humanLethalStartFor = (side: "ally" | "enemy", index: number): number | null => {
    const unitKey = `${side}-${index}`;
    const targeted = event.targetSide === side && event.target === index;
    const damaging =
      event.kind === "attack" || event.kind === "critical" || event.kind === "weak" || event.kind === "skill";
    if (targeted && damaging && event.ko) {
      let startTick = humanLethalStart.current.get(unitKey);
      if (startTick === undefined) {
        const actorCharacter =
          event.side === "ally" ? humanIdleCharacterForUnit(layout.allies[event.actor]?.unit ?? {}) : null;
        const actorAttackStart = attackStartFor(event.side, event.actor);
        startTick =
          actorCharacter && actorAttackStart !== null
            ? actorAttackStart + HUMAN_ATTACK_WINDOW.hitUpdate
            : animTick;
        humanLethalStart.current.set(unitKey, startTick);
      }
      return startTick;
    }
    return humanLethalStart.current.get(unitKey) ?? null;
  };

  /**
   * PASS 15 COMMAND 15.5: when a human ally's damage reaction starts.
   *
   * `UpdateAttacking 0x1585568` applies the hit at native attack update 11, so when the attacker is
   * one of the implemented human attacks the target's reaction begins at
   * `attacker.attackStartTick + 11` - the same update the attacker's animation marks as the hit.
   * For any other source (every monster today) the hit timing is NOT recovered, so the reaction
   * begins on the update the replay event becomes current and the state is exported as
   * `unrecovered-source-hit`. Either way this is visual playback only: the event, the HP state and
   * the roster are untouched.
   */
  const humanDamageStartFor = (side: "ally" | "enemy", index: number): number | null => {
    const lethalStart = humanLethalStartFor(side, index);
    if (lethalStart !== null) return lethalStart;
    const unitKey = `${side}-${index}`;
    const targeted = event.targetSide === side && event.target === index;
    const damaging =
      event.kind === "attack" || event.kind === "critical" || event.kind === "weak" || event.kind === "skill";
    if (!targeted || !damaging) {
      humanDamageStart.current.delete(unitKey);
      return null;
    }
    let startTick = humanDamageStart.current.get(unitKey);
    if (startTick === undefined) {
      const actorCharacter =
        event.side === "ally" ? humanIdleCharacterForUnit(layout.allies[event.actor]?.unit ?? {}) : null;
      const actorAttackStart = attackStartFor(event.side, event.actor);
      startTick =
        actorCharacter && actorAttackStart !== null
          ? actorAttackStart + HUMAN_ATTACK_WINDOW.hitUpdate
          : animTick;
      humanDamageStart.current.set(unitKey, startTick);
    }
    return startTick;
  };

  /** The native visual state of a human ally at the current anim tick. */
  const humanAnimationFor = (
    side: "ally" | "enemy",
    index: number,
    character: HumanIdleCharacter,
  ): HumanNativeVisual => {
    const unit = beatState.units.find((entry) => entry.side === side && entry.index === index);
    const previousUnit = beats[Math.max(0, step - 1)].units.find(
      (entry) => entry.side === side && entry.index === index,
    );
    const visualStartCell = previousUnit?.cell ?? unit?.cell ?? { column: 0, row: 0 };
    const visualStartWorld: HumanWorldPosition = {
      x: visualStartCell.column * FORMATION_CONSTANTS.cellSize,
      y: 0,
      z: visualStartCell.row * FORMATION_CONSTANTS.cellSize,
    };
    const damageStart = humanDamageStartFor(side, index);
    const lethalStart = humanLethalStartFor(side, index);
    const lethal = lethalStart !== null || Boolean(damageStart !== null && unit && !unit.alive);
    const baseInput = {
      character,
      tick: animTick,
      team: side === "ally" ? 0 : 1,
      /*
       * The unit's own cell is `board[7]` at the moment state 8 starts. `simulateOccupancy` keeps a
       * defeated unit's cell, so the flight starts from the cell it actually occupied.
       */
      startCell: visualStartCell,
      rivalTeamMemberCount: encounter.enemies.length,
      // The allies' recovered battle facing is UP (`BattleForm.Init` -> `Invert(DOWN)`), and the
      // knock-down spin starts from the facing the unit had when the reaction began.
      entryDirection: side === "ally" ? 0 : 2,
      attackStartTick: damageStart === null ? attackStartFor(side, index) : null,
      damageStartTick: damageStart,
      lethal,
    } as const;
    const unitKey = `${side}-${index}`;
    const reviveEvent =
      event.targetSide === side && event.target === index && event.revive === true;
    let revival = humanRevivalStart.current.get(unitKey);
    if (reviveEvent && !revival) {
      const baseline = humanNativeVisual(baseInput);
      if (baseline.phase === "leaving") {
        revival = {
          tick: animTick,
          start: visualStartWorld,
          target: { cell: visualStartCell, world: visualStartWorld },
          walkDirection: baseline.direction,
          unsupported: true,
        };
      } else {
        const queue = nativeQueueingPosition({
          side,
          targetIndex: index,
          units: beatState.units,
          rowOffset,
        });
        revival = {
          tick: animTick,
          start: visualStartWorld,
          target: { cell: queue.cell, world: queue.world },
          walkDirection: baseline.direction,
          unsupported: false,
        };
      }
      humanRevivalStart.current.set(unitKey, revival);
    }
    return humanNativeVisual({
      ...baseInput,
      reviveStartTick: revival && !revival.unsupported ? revival.tick : null,
      reviveStartWorld: revival?.start ?? null,
      reviveTarget: revival && !revival.unsupported ? revival.target : null,
      reviveDirection: revival?.walkDirection ?? 0,
    });
  };

  /**
   * PASS 15 COMMAND 15.5 visual retention.
   *
   * The native lethal path keeps a defeated human's sprite alive through 7 updates of damage, 101
   * updates of knock-down/down and then the leaving flight (COMMAND 15.3/15.4), and nothing in the
   * fighter path removes it. The replay used to delete a KO'd unit at its KO beat, which is not the
   * recovered behaviour, so a human ally with a recovered reaction is kept in the visual scene. This
   * is presentation only: the combat roster, the HP state, the occupancy simulator and every
   * targeting rule are untouched, and monster KO keeps the existing removal.
   */
  const humanVisualRetained = (
    side: "ally" | "enemy",
    unit: { jobName?: string; rank?: string },
  ) => side === "ally" && humanIdle && humanIdleCharacterForUnit(unit) !== null;

  /**
   * The per-line SEB records for the unit's current visual state. The idle and attack states read a
   * `HumanClip`; the PASS 15 COMMAND 15.5 reaction states read one of the four `base + direction`
   * variants of a reaction clip (with the `,u` mirror already folded into `reversU`).
   */
  const humanLinesFor = (
    side: "ally" | "enemy",
    index: number,
    character: HumanIdleCharacter,
  ): HumanIdleLine[] => {
    const visual = humanAnimationFor(side, index, character);
    if (visual.phase === "revival-moving" && visual.revival) {
      return humanWalkLinesAt(visual.revival.direction, visual.revival.frame);
    }
    return visual.reactionId
      ? humanReactionLinesAt(visual.reactionId, visual.direction, visual.frame)
      : humanLineRecordsAt(visual.clipId ? HUMAN_CLIPS[visual.clipId] : HUMAN_IDLE_CLIP, visual.frame);
  };

  /**
   * Animation state from the recovered behaviour map: moving -> behaviour 2,
   * attacking -> weapon behaviour, hit -> behaviour 30, knock-down -> behaviour 28.
   * Monsters only have the wait (base 0) and attack (base 16) groups, so every
   * other state reuses the wait clip.
   */
  const animationFor = (side: "ally" | "enemy", index: number) => {
    const unit = beatState.units.find((entry) => entry.side === side && entry.index === index);
    let state: AnimationStateId = "idle";
    if (unit && !unit.alive) state = "knockdown";
    else if (unit?.movedThisBeat) state = "walk";
    else if (event.side === side && event.actor === index) {
      state = event.kind === "skill" ? "skill" : "attack";
    } else if (event.targetSide === side && event.target === index) {
      if (event.ko) state = "knockdown";
      else if (event.kind === "attack" || event.kind === "critical" || event.kind === "weak") state = "hit";
    }

    const isAttack = state === "attack" || state === "skill" || state === "hit";
    /**
     * Native direction: each team's entity direction comes from the recovered battle setup
     * (FighterSystem.InitFighters stores Invert(slot direction)), and the direction selects the SEB
     * variant: UP -> the _up asset, DOWN -> the same _right asset with the ,u (u-axis) mirror.
     * The mirror is a property of the resource, so every SEB line of the unit is mirrored.
     */
    const direction = BATTLE_DIRECTIONS[side];
    const variant = MONSTER_DIRECTION_VARIANTS[direction];
    const sprite = monsterSpriteById(encounter.enemies[index]?.monsterId ?? null);
    const isMonster = side === "enemy" || Boolean(monsterSpriteById(encounter.allies[index]?.monsterId ?? null));
    if (!isMonster) {
      return {
        state,
        offset: { x: 0, y: 0 },
        clip: null,
        sebTick: 0,
        sebPhase: 0,
        direction,
        mirror: { u: false, v: false },
        trace: [] as string[],
      };
    }

    const clip = monsterClipForDirection(isAttack ? "attack" : "wait", direction);
    /**
     * Native frame progression: `ChangeAnimation` restarts the unit's `SebComponent.frame` at 0, and
     * `AnimationSystem.Update` adds `rate` (1 for monsters) every update, wrapping at the clip's
     * maxFrame. Units therefore keep independent phases and only resynchronise when their animation
     * changes - exactly what this per-unit start tick reproduces.
     */
    const unitKey = `${side}-${index}`;
    const stored = unitAnimationStart.current.get(unitKey);
    const startTick = !stored || stored.clipId !== clip.id ? animTick : stored.startTick;
    unitAnimationStart.current.set(unitKey, { clipId: clip.id, startTick });
    const ticksSinceAnimationChange = animTick - startTick;
    const sebTick = ticksSinceAnimationChange;
    const sebPhase = nativeAnimationFrame(clip, ticksSinceAnimationChange);
    const frame = clipFrameAt(clip, sebTick);
    const first = clipFrameAt(clip, 0);
    const unitScale = ((sprite?.height ?? 30) / 60) * (side === "enemy" && encounter.enemies[index]?.isBoss ? 2.2 : 1.7);
    const monsterId = side === "enemy" ? encounter.enemies[index]?.monsterId ?? null : encounter.allies[index]?.monsterId ?? null;
    const monster = monsterId === null ? undefined : MONSTER_DATA_ENCOUNTERS[monsterId];
    const bodySheet = monster ? monsterSheetFor(monster.img, monster.size) : null;
    const shadowSheet = monster ? shadowSheetFor(monster.size) : null;
    const trace = [MONSTER_LINES.shadow, MONSTER_LINES.body].map((line) => {
      const sheet = line === MONSTER_LINES.shadow ? shadowSheet : bodySheet;
      const record = sebSpriteAt(clip, sebTick, line);
      const prefix =
        `m${monsterId ?? "?"} ${state} dir ${direction}(${variant.index}) asset ${variant.asset}` +
        `${variant.assetFlags.length ? "," + variant.assetFlags.join(",") : ""} ` +
        `mirror u${variant.mirror.u ? 1 : 0} v${variant.mirror.v ? 1 : 0} ` +
        `${clip.seb} f${sebPhase} L${line}`;
      if (!record) return `${prefix} · no record (line draws nothing)`;
      const cell = sheet ? monsterCellAtSebRect(sheet, record) : null;
      return (
        `${prefix} tex${record.tex} u${record.u} v${record.v} w${record.w} h${record.h} ` +
        `tX${record.transX} tY${record.transY} rU${record.reversU} rV${record.reversV} ` +
        `img ${sheet?.name ?? "none"} line +(${record.transX},${record.transY}) ` +
        (cell ? `-> opt d${cell.dest.x},${cell.dest.y} src ${cell.src.x},${cell.src.y} ${cell.src.w}x${cell.src.h}` : "-> no recovered cell") +
        ` tick${ticksSinceAnimationChange} global${animTick} rate${NATIVE_ANIMATION.monsterRate}`
      );
    });
    return {
      state,
      offset: {
        x: (frame.offsetX - first.offsetX) * unitScale,
        y: (frame.offsetY - first.offsetY) * unitScale,
      },
      clip,
      sebTick,
      sebPhase,
      direction,
      mirror: variant.mirror,
      trace,
    };
  };

  /**
   * One shared per-line battle sprite queue (PASS 14 COMMAND 14.8).
   *
   * Native paints this way: `RenderSystem.InsertZSortObject(Entity)` 0x15D0214 queues one entry per
   * SEB line of every entity into one array and `AppData.DrawZSortObjects` 0x167110C sorts that
   * array by the entry's depth alone. The previous structure - one DOM container per unit, allies in
   * roster order then enemies - could not express that, which is why the Wairo Tank's large body was
   * painted over neighbours whose cell depth puts them in front of it.
   *
   * Depth is `24 * (cell.xi + cell.yi) + height + extra + add + lineIndex` from the unit's LIVE cell
   * for the rendered replay state (never the initial slot after movement). Insertion sequence is the
   * website's own (allies then enemies, ascending SEB line index); equal depths keep that sequence,
   * because native's own equal-depth order is UNREPRODUCED (an unstable hand-written QuickSort over a
   * `HashSet<Entity>` enumeration order - see `lib/battle-depth-queue.ts`). No secondary game field
   * is ever added as a tiebreak.
   */
  const humanPartRules = useHumanPartRules();
  const spriteQueue = (() => {
    if (!nativeMode) return [] as ReturnType<typeof buildBattleSpriteQueue<SpriteLinePayload>>;
    const items: BattleQueueItem<SpriteLinePayload>[] = [];
    const pushUnit = (side: "ally" | "enemy", placement: Placement) => {
      const lines: { lineIndex: number; payload: SpriteLinePayload }[] = [];
      const human = humanIdle ? humanIdleCharacterForUnit(placement.unit) : null;
      const anim = animationFor(side, placement.index);
      let liveWorldPosition: HumanWorldPosition | null = null;
      if (human) {
        const humanAnim = humanAnimationFor(side, placement.index, human);
        liveWorldPosition = humanAnim.revival?.world ?? humanAnim.leaving?.world ?? null;
        const storedRevival = humanRevivalStart.current.get(`${side}-${placement.index}`);
        /*
         * PASS 15 COMMAND 15.5: the reaction states run through the same per-line resolver as the
         * idle and attack clips - only the record source changes. The reaction clips carry four
         * `base + direction` variants and the `,u` ones mirror, so `humanReactionLinesAt` returns
         * records whose `reversU` already carries the flip; nothing downstream needs a new path.
         */
        const humanLines =
          humanAnim.phase === "revival-moving" && humanAnim.revival
            ? humanWalkLinesAt(humanAnim.revival.direction, humanAnim.revival.frame)
            : humanAnim.reactionId
              ? humanReactionLinesAt(humanAnim.reactionId, humanAnim.direction, humanAnim.frame)
              : humanLineRecordsAt(
                  humanAnim.clipId ? HUMAN_CLIPS[humanAnim.clipId] : HUMAN_IDLE_CLIP,
                  humanAnim.frame,
                );
        const resolved = humanPartRules
          ? humanIdleResolution(human, humanPartRules, humanLines)
          : null;
        const draws = resolved?.draws ?? [];
        for (const draw of draws) {
          lines.push({
            lineIndex: draw.line,
            payload: {
              kind: "human",
              placement,
              characterId: human.id,
              draws: draws.length,
              skipped: resolved?.skipped.length ?? 0,
              draw,
              clipId: humanAnim.clipId,
              reactionId: humanAnim.reactionId,
              seb: humanAnim.seb,
              phase: humanAnim.nativeState,
              frame: humanAnim.frame,
              attackUpdate: humanAnim.attackUpdate,
              reactionUpdate: humanAnim.reactionUpdate,
              knockdownUpdate: humanAnim.knockdownUpdate,
              direction: humanAnim.direction,
              flip: humanAnim.flip,
              behaviour: humanAnim.behaviour,
              damageLiftZ: humanAnim.damageLiftZ,
              leaving: humanAnim.leaving,
              revival: humanAnim.revival ?? null,
              revivalState: storedRevival?.unsupported
                ? "unsupported-during-leaving"
                : humanAnim.revival?.state ?? null,
              rivalTeamMemberCount: encounter.enemies.length,
            },
          });
        }
      } else if (placement.unit.monsterId !== null && anim.clip) {
        const monster = MONSTER_DATA_ENCOUNTERS[placement.unit.monsterId];
        const bodySheet = monster ? monsterSheetFor(monster.img, monster.size) : null;
        const shadowSheet = monster ? shadowSheetFor(monster.size) : null;
        for (const line of [MONSTER_LINES.shadow, MONSTER_LINES.body]) {
          const record = sebSpriteAt(anim.clip, anim.sebTick, line);
          const sheet = line === MONSTER_LINES.shadow ? shadowSheet : bodySheet;
          const optCell = record && sheet ? monsterCellAtSebRect(sheet, record) : null;
          if (!record || !optCell) continue;
          lines.push({
            lineIndex: line,
            payload: { kind: "monster", placement, record, cell: optCell, mirror: anim.mirror },
          });
        }
      }
      if (lines.length === 0) return;
      items.push({
        unitId: `${side}-${placement.index}`,
        side,
        unitIndex: placement.index,
        // the live cell of this replay state, so occupancy/advance changes move the depth with it
        cell: placement.cell ?? placement.slot,
        /*
         * PASS 15 COMMAND 15.9: only the leaving fighter drives its own PositionComponent, so only
         * that unit supplies a live world position. Every stationary unit leaves this null and keeps
         * the exact PASS 14 cell-depth formula.
         */
        worldPosition: liveWorldPosition,
        height: 0,
        lines,
      });
    };
    for (const placement of layout.allies.filter(
      (entry) => labMode || !entry.dead || humanVisualRetained("ally", entry.unit),
    )) {
      pushUnit("ally", placement);
    }
    for (const placement of layout.enemies.filter((entry) => labMode || !entry.dead)) {
      pushUnit("enemy", placement);
    }
    return buildBattleSpriteQueue(items);
  })();
  /** units whose sprite lines the shared queue already paints (so their box must contribute none) */
  const queuedUnits = new Set(spriteQueue.map((entry) => `${entry.side}-${entry.unitIndex}`));

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <PageHeader
        icon={<Swords className="w-5 h-5" />}
        title="Battle replay (first pass)"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {labMode ? (
              <>
                <Badge variant="outline" className="font-mono text-[10px]">lab surface</Badge>
                <Button type="button" size="sm" variant="outline" onClick={() => setLabMode(false)}>
                  Recovered Replay
                </Button>
              </>
            ) : (
              <Badge variant="secondary" className="font-mono text-[10px]">Recovered Replay</Badge>
            )}
          </div>
        }
      >
        {labMode ? (
          <>
            <p>
              A first-pass replay of how a Wairo Dungeon or Kairo Room fight is presented. One shared
              renderer draws both encounters; only the encounter data (lineup, boss, timeline) changes.
            </p>
            <p>
              Current working conclusion: Wairo and Kairo appear to share the same battle presentation
              system; differences are primarily encounter content rather than renderer structure.
            </p>
            <p>
              Basis for the shared-arena part: recordings of one Kairo Room fight (average Lv 251) and
              one Wairo Dungeon fight (average Lv 83) both show the same red-carpet arena art, the same
              BOSS banner over the boss, and the same side layout (allies left, 5-column enemy block
              right). The screen mapping from the recovered native cells is still a presentation choice.
            </p>
            <p className="text-xs">
              This is not a claim of exact native animation, timing or screen placement. Every layer and
              asset below carries a CONFIRMED / SUPPORTED / UNKNOWN tag.
            </p>
          </>
        ) : (
          <p>
            One locked configuration of the recovered battle presentation: the Wairo Tank encounter
            (the only roster whose enemies all have recovered battle art), bg_100 under the
            recording-measured ~240-logical window, the frozen native human idle, and the replay's own
            state driving the units, the HUD and the roster. Nothing on this page overrides it.
          </p>
        )}
      </PageHeader>

      {/*
        Lab surface only: the encounter and arena pickers are content experiments. Recovered Replay
        locks both - `wairo-tank` is the only encounter whose whole roster has recovered monster
        art, and bg_100 is the only arena the supplied recordings show.
      */}
      {labMode && (
        <>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Encounter</CardTitle>
          <CardDescription>
            Rosters and boss ids come from the original SpecialBoss rows; names come from the shared
            monster sprite data.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {BATTLE_ENCOUNTERS.map((entry) => (
              <Button
                key={entry.id}
                type="button"
                variant={entry.id === encounterId ? "default" : "outline"}
                className="h-auto flex-col items-start gap-1 py-2 text-left"
                onClick={() => setEncounterId(entry.id)}
              >
                <span className="text-xs font-semibold">{entry.title}</span>
                <span className="text-[10px] opacity-80">
                  {entry.family === "kairo-room" ? "Kairo Room" : "Wairo Dungeon"} · chip {entry.chipId}
                </span>
              </Button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="outline" className="font-mono text-[10px]">boss {bossName}</Badge>
            <Badge variant="outline" className="font-mono text-[10px]">level {encounter.level}</Badge>
            <Badge variant="outline" className="font-mono text-[10px]">boss level {encounter.bossLevel}</Badge>
            <span>
              {encounter.allies.length} allies · {encounter.enemies.length} enemies (boss included)
            </span>
          </div>
          <p className="text-[11px] text-muted-foreground">Source: {encounter.source}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Arena</CardTitle>
          <CardDescription>
            Both recorded fights (Kairo Room and Wairo Dungeon) show the same red-carpet arena art,
            which matches bg_100, so the replay defaults to it for every encounter. All eleven
            original battle-group backgrounds stay selectable because the per-fight selection rule
            is not traced.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {ARENAS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setArenaId(entry.id)}
                title={entry.look}
                className={`rounded-md border p-1 transition-colors ${
                  entry.id === arenaId ? "border-primary ring-2 ring-primary/40" : "border-border hover:border-primary/40"
                }`}
              >
                <img
                  src={entry.file}
                  alt={entry.id}
                  className="h-12 w-auto"
                  style={{ imageRendering: "pixelated" }}
                />
                <div className="mt-0.5 text-center text-[9px] text-muted-foreground">{entry.id}</div>
                {entry.seenInRecordings && (
                  <div className="text-center text-[8px] text-primary">shared · footage</div>
                )}
              </button>
            ))}
          </div>
          <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-xs">
            <EvidenceBadge level={ARENA_SELECTION_EVIDENCE.evidence} />
            <span className="text-muted-foreground">{ARENA_SELECTION_EVIDENCE.note}</span>
          </div>
        </CardContent>
      </Card>
        </>
      )}

      {!labMode && (
        <Card data-recovered-replay="1">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Recovered Replay</CardTitle>
            <CardDescription>
              One locked, faithful configuration - nothing on this page overrides it. The stage shows
              the replay's own state: every unit that has not left the field, on the cell the state
              model gives it, and no overlay that is still a placeholder.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="rounded-md border px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Encounter</div>
                <div className="font-medium" data-recovered-encounter={encounter.id}>
                  {encounter.title}
                </div>
                <div className="text-muted-foreground">
                  {encounter.family === "kairo-room" ? "Kairo Room" : "Wairo Dungeon"} · chip{" "}
                  {encounter.chipId} · {encounter.allies.length} allies · {encounter.enemies.length}{" "}
                  enemies (boss included) · level {encounter.level} / boss {encounter.bossLevel}
                </div>
              </div>
              <div className="rounded-md border px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Locked to the recovered values</div>
                <div className="text-muted-foreground">
                  arena {arena.id} · view profile {viewProfile.label} · native scene + frozen native
                  human idle · full roster · state-driven cells · no debug overlay, no comparison
                  renderer
                </div>
              </div>
            </div>
            <div className="flex items-start gap-2 rounded-md border px-3 py-2">
              <EvidenceBadge level="SUPPORTED" />
              <span className="text-muted-foreground">
                This is the only encounter whose enemies all have recovered battle art. The other
                three rosters are dominated by monsters whose battle sheet is not recovered yet, so
                their units would be drawn from no rule at all - which is why this mode does not
                offer them. Units that leave the field (timeline knock-outs) are removed here instead
                of being dimmed, and the state model behind the cells is the documented
                rule-faithful approximation (advance ordering is unresolved).
              </span>
            </div>
            <div className="flex items-start gap-2 rounded-md border px-3 py-2">
              <EvidenceBadge level="SUPPORTED" />
              <span className="text-muted-foreground">
                The window is the ~{viewProfile.logicalViewWidth}-logical-wide surface measured from
                the supplied recording, so the far column of the deeper enemy rows sits outside it -
                exactly as in the footage, where the player pans the camera by touch
                (<code>BattleForm.OnTouchCamera</code>). This replay does not implement that pan yet,
                so nothing beyond logical X {viewProfile.logicalViewWidth} can be brought into view.
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" size="sm" variant="outline" onClick={() => setLabMode(true)}>
                Show lab controls
              </Button>
              <span className="text-[11px] text-muted-foreground">
                Everything experimental - encounter and arena pickers, view profiles, toggles, the
                evidence tables and the URL comparison flags - lives on that surface.
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Replay stage</CardTitle>
          <CardDescription>
            {labMode
              ? "Shared layers: background, units, effects, combat text, skill balloon, optional HUD."
              : "Recovered Replay shows the arena, the units and the boss banner; every other layer is a placeholder and stays on the lab surface."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/*
            Lab surface only: the view profile is a platform parameter, not a rendering choice the
            viewer should expose while claiming to be faithful (Recovered Replay pins the recorded
            ~240 window).
          */}
          {labMode && (
            <>
          {/*
            View profile: how much of the logical battle space the surface window exposes. The
            internal 481x197 render space, the fighter coordinates and the camera never change with
            it - only the window, the backdrop's native origin and the presentation scale do.
          */}
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              {VIEW_PROFILES.map((profile) => (
                <button
                  key={profile.id}
                  type="button"
                  data-view-profile-option={profile.id}
                  onClick={() => setViewProfileId(profile.id)}
                  title={profile.note}
                  className={`rounded-md border px-3 py-2 text-left transition-colors ${
                    profile.id === viewProfile.id
                      ? "border-primary ring-2 ring-primary/40"
                      : "border-border hover:border-primary/40"
                  }`}
                >
                  <span className="block text-xs font-medium">{profile.label}</span>
                  <span className="block font-mono text-[10px] text-muted-foreground">
                    logical width {profile.logicalViewWidth} · bg origin{" "}
                    {backgroundXForViewWidth(profile.logicalViewWidth)} · vertical{" "}
                    {profile.sourceHeight}→{profile.presentationHeight}
                  </span>
                </button>
              ))}
            </div>
            <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-[11px]">
              <EvidenceBadge level={viewProfile.evidence} />
              <span className="text-muted-foreground">{viewProfile.note}</span>
            </div>
            <p className="font-mono text-[10px] text-muted-foreground">
              backgroundX = trunc(({viewProfile.logicalViewWidth} − {SCENE_BACKGROUND.width}) / 2) ={" "}
              {backgroundX} · visible bg_100 source X {visibleBackground.from}..{visibleBackground.to} ·
              vertical presentation {viewProfile.presentationHeight}/{viewProfile.sourceHeight} ={" "}
              {presentationRatio.toFixed(5)} · fighters keep nativeViewPos (backgroundX is not applied to
              them)
            </p>
          </div>
            </>
          )}
          {!labMode && (
            <div
              data-recovered-overlay-notice="1"
              className="flex items-start gap-2 rounded-md border px-3 py-2 text-[11px]"
            >
              <EvidenceBadge level="UNKNOWN" />
              <span className="text-muted-foreground">
                Recovered Replay hides visual effects and combat overlays that have not yet been
                recovered from the native game.
              </span>
            </div>
          )}
          {/*
            Presentation wrapper = the surface window: it exposes logical X 0..logicalViewWidth and
            reserves the presented band height (no overlap with the page flow). Anything outside the
            window is clipped here, exactly like the native surface. The 1px frame lives outside the
            transform, so it cannot introduce a second X/Y scale.
         */}
          <BattleStage
            hostRef={sceneHostRef}
            viewProfileId={viewProfile.id}
            logicalViewWidth={viewProfile.logicalViewWidth}
            sourceHeight={viewProfile.sourceHeight}
            sceneScale={sceneScale}
            presentationScale={presentationScale}
            presentationRatio={presentationRatio}
            backgroundX={backgroundX}
            arenaFile={arena.file}
            rowOffset={rowOffset}
            leavingFixtures={leavingFixtures}
            revivalFixtures={revivalFixtures}
          >
          {/*
            The logical scene: fixed 481x197 px (the recovered offscreen basis), scaled once by the
            shared presentation transform. The backdrop keeps its native 1:1 size and native origin
            `backgroundX`, so scenery and fighters share one transform while only the backdrop is
            translated.
          */}
            {/*
              The one shared battle-sprite layer: every visible unit's drawable SEB line is one entry
              here, already ordered by the recovered native depth key. Painting is simply DOM order,
              so lines of different units interleave exactly as the native queue does. Gauges, the
              BOSS banner, the replay controls and every status overlay stay outside this layer.
            */}
            {spriteQueue.length > 0 && (
              <div
                className="pointer-events-none absolute left-0 top-0"
                data-battle-sprite-layer
                data-battle-sprite-order="depth-asc"
                data-battle-sprite-entries={spriteQueue.length}
                style={{ width: 0, height: 0 }}
              >
                {spriteQueue.map((entry) => {
                  const live = entry.worldPosition
                    ? nativeWorldViewPos(
                        entry.worldPosition.x,
                        entry.worldPosition.y,
                        entry.worldPosition.z,
                        rowOffset,
                      )
                    : null;
                  const leaving =
                    entry.payload.kind === "human" ? entry.payload.leaving : null;
                  return (
                    <div
                      key={entry.key}
                      className="absolute"
                      data-battle-sprite-entry
                      data-side={entry.side}
                      data-unit-index={entry.unitIndex}
                      data-line-index={entry.lineIndex}
                      data-base-depth={entry.baseDepth}
                      data-depth={entry.depth}
                      data-insert-order={entry.order}
                      data-cell={`${entry.cell.column},${entry.cell.row}`}
                      data-live-position={entry.worldPosition ? "1" : undefined}
                      data-world-position={
                        entry.worldPosition
                          ? `${entry.worldPosition.x},${entry.worldPosition.y},${entry.worldPosition.z}`
                          : undefined
                      }
                      style={{
                        left: live ? `${live.x}px` : entry.payload.placement.left,
                        top: live ? `${live.y}px` : entry.payload.placement.top,
                        /*
                         * The recovered damage reaction writes `PositionComponent.offsetZ (+0x24)`,
                         * a world-z draw offset. The scene maps world (x,y,z) to screen as
                         * `(x - z, (x + z) / 2 - y)`, so a z offset of `dz` moves the sprite by
                         * `(-dz, +dz/2)`; the arc is zero outside the damage state, which is why the
                         * unit returns exactly to its cell origin. A live leaving position already
                         * carries the full world vector, so it never also applies this arc.
                         */
                        transform:
                          !entry.worldPosition &&
                          entry.payload.kind === "human" &&
                          entry.payload.damageLiftZ
                            ? `translate(${-entry.payload.damageLiftZ}px, ${entry.payload.damageLiftZ / 2}px)`
                            : undefined,
                      }}
                    >
                      <div className="relative" style={{ width: 0, height: 0 }}>
                        {entry.payload.kind === "human" ? (
                          <div
                            className="relative"
                            style={{ width: 0, height: 0 }}
                            data-human-idle={entry.payload.characterId}
                            data-human-clip={entry.payload.clipId}
                            data-human-reaction={entry.payload.reactionId ?? ""}
                            data-human-native-state={entry.payload.phase}
                            data-human-behaviour={entry.payload.behaviour}
                            data-human-direction={entry.payload.direction}
                            data-human-flip={entry.payload.flip ? "1" : "0"}
                            data-human-seb={entry.payload.seb}
                            data-human-phase={entry.payload.phase}
                            data-human-frame={entry.payload.frame}
                            data-attack-update={entry.payload.attackUpdate ?? ""}
                            data-human-reaction-update={entry.payload.reactionUpdate ?? ""}
                            data-human-knockdown-update={entry.payload.knockdownUpdate ?? ""}
                            data-human-damage-lift={entry.payload.damageLiftZ}
                            data-human-leaving={leaving ? "1" : ""}
                            data-human-leaving-update={leaving?.frame ?? ""}
                            data-human-projectile-frame={leaving?.frame ?? ""}
                            data-human-projectile-max-frame={leaving?.maxFrame ?? ""}
                            data-human-world-x={entry.worldPosition?.x ?? ""}
                            data-human-world-y={entry.worldPosition?.y ?? ""}
                            data-human-world-z={entry.worldPosition?.z ?? ""}
                            data-human-rival-team-member-count={entry.payload.rivalTeamMemberCount}
                            data-human-leaving-frame={leaving?.frame ?? ""}
                            data-human-leaving-updates={leaving?.updates ?? ""}
                            data-human-leaving-impacted={leaving ? (leaving.impacted ? "1" : "0") : ""}
                            data-human-leaving-height={leaving?.height ?? ""}
                            data-human-leaving-start={
                              leaving ? `${leaving.start.x},${leaving.start.y},${leaving.start.z}` : ""
                            }
                            data-human-leaving-end={
                              leaving ? `${leaving.end.x},${leaving.end.y},${leaving.end.z}` : ""
                            }
                            data-human-leaving-world={
                              leaving ? `${leaving.world.x},${leaving.world.y},${leaving.world.z}` : ""
                            }
                            data-human-revival-state={entry.payload.revivalState ?? ""}
                            data-human-move-frame={entry.payload.revival?.moveFrame ?? ""}
                            data-human-move-target-x={entry.payload.revival?.target.x ?? ""}
                            data-human-move-target-z={entry.payload.revival?.target.z ?? ""}
                            data-human-move-target-cell={
                              entry.payload.revival
                                ? `${entry.payload.revival.targetCell.column},${entry.payload.revival.targetCell.row}`
                                : ""
                            }
                            data-native-hit-update={HUMAN_ATTACK_WINDOW.hitUpdate}
                            data-native-attack-window={HUMAN_ATTACK_WINDOW.updates}
                            data-native-damage-window={HUMAN_DAMAGE_WINDOW.updates}
                            data-native-knockdown-down-at={HUMAN_KNOCKDOWN.downFrom}
                            data-native-knockdown-leaving-at={HUMAN_KNOCKDOWN.leavingAt}
                            data-native-leaving-kind={HUMAN_LEAVING.kind}
                            data-native-leaving-min-height={HUMAN_LEAVING.minHeight}
                            data-native-leaving-max-height={HUMAN_LEAVING.maxHeight}
                            data-human-draws={entry.payload.draws}
                            data-human-skipped={entry.payload.skipped}
                          >
                            <HumanIdleLineLayer draw={entry.payload.draw} />
                          </div>
                        ) : (
                          <MonsterLineSprite
                            record={entry.payload.record}
                            cell={entry.payload.cell}
                            mirror={entry.payload.mirror}
                          />
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {layout.allies
              .filter(
                (placement) => labMode || !placement.dead || humanVisualRetained("ally", placement.unit),
              )
              .map((placement) => {
              const anim = animationFor("ally", placement.index);
              const human = humanIdle ? humanIdleCharacterForUnit(placement.unit) : null;
              const gaugeUnit = beatState.units.find(
                (unit) => unit.side === "ally" && unit.index === placement.index,
              );
              const fighterGauge =
                gaugeUnit && isMostFrontUnit("ally", gaugeUnit.cell.row, rowOffset)
                  ? replayFighterVitals(encounter.timeline, "ally", placement.index, step)
                  : null;
              return (
                <UnitSprite
                  key={`ally-${placement.index}`}
                  placement={placement}
                  side="ally"
                  showLabel={showUnitLabels}
                  debugFormation={debugFormation}
                  debugSeb={debugSeb}
                  animState={anim.state}
                  animOffset={anim.offset}
                  sebClip={anim.clip}
                  sebFrame={anim.sebTick}
                  sebMirror={anim.mirror}
                  sebTrace={anim.trace}
                  compositeTick={!human && nativeAlly && placement.index === 0 ? animTick : undefined}
                  humanCharacter={human}
                  humanLines={human ? humanLinesFor("ally", placement.index, human) : null}
                  fighterGauge={fighterGauge}
                  nativeBody={nativeMode}
                  humanRuntimeParts={humanRuntimeParts}
                  anchorBottom={nativeMode}
                  allyFacing={allyFacing}
                  queueMode={queuedUnits.has(`ally-${placement.index}`)}
                />
              );
            })}
            {layout.enemies
              .filter((placement) => labMode || !placement.dead)
              .map((placement) => {
              const anim = animationFor("enemy", placement.index);
              const gaugeUnit = beatState.units.find(
                (unit) => unit.side === "enemy" && unit.index === placement.index,
              );
              const fighterGauge =
                gaugeUnit && isMostFrontUnit("enemy", gaugeUnit.cell.row, rowOffset)
                  ? replayFighterVitals(encounter.timeline, "enemy", placement.index, step)
                  : null;
              return (
                <UnitSprite
                  key={`enemy-${placement.index}`}
                  placement={placement}
                  side="enemy"
                  showLabel={labMode && (showUnitLabels || Boolean(placement.unit.isBoss))}
                  debugFormation={debugFormation}
                  debugSeb={debugSeb}
                  animState={anim.state}
                  animOffset={anim.offset}
                  sebClip={anim.clip}
                  sebFrame={anim.sebTick}
                  sebMirror={anim.mirror}
                  sebTrace={anim.trace}
                  fighterGauge={fighterGauge}
                  nativeSize={nativeMode ? nativeMonsterSize : undefined}
                  nativeBody={nativeMode}
                  anchorBottom={nativeMode}
                  queueMode={queuedUnits.has(`enemy-${placement.index}`)}
                />
              );
            })}

            {layout.enemies
              .filter((placement) => placement.unit.isBoss && (labMode || !placement.dead))
              .map((placement) => (
                <img
                  key="boss-banner"
                  src={HUD_SPRITES.bossBanner}
                  alt="BOSS"
                  className="absolute -translate-x-1/2 -translate-y-full"
                  style={{
                    left: placement.left,
                    top: `calc(${placement.top} - 18px)`,
                    imageRendering: "pixelated",
                    transform: "translate(-50%, -100%) scale(1.6)",
                  }}
                />
              ))}

            {/*
              Combat overlays are the replay's own presentation stand-ins, not recovered native
              draws: the damage-number sheet and combat-text rows are extracted assets, but the
              number sizing/placement, the guard/status text, the skill balloon and the impact
              effect's frame slicing have no recovered draw rule yet. They stay on the lab surface
              only (see the notice above the stage in Recovered Replay).
            */}
            {labMode && (
              <EventOverlay
                event={event}
                encounter={encounter}
                layout={layout}
                showEffects={showEffects}
                showArrows={showArrows}
                showBalloon={showBalloon}
              />
            )}

            {debugGrid && (
              <div
                className="pointer-events-none absolute left-1 top-1 border border-cyan-400/80 bg-black/50"
                style={{ width: BATTLE_VIEW.width * 0.75, height: BATTLE_VIEW.height * 0.75 }}
              >
                {Array.from({ length: FORMATION_CONSTANTS.mapHeight }, (_, row) =>
                  Array.from({ length: FORMATION_CONSTANTS.mapWidth }, (_, column) => {
                    const pos = nativeViewPos(column, row, rowOffset);
                    const isOrigin = column === 0 && row === 0;
                    const isX = column === 1 && row === 0;
                    const isY = column === 0 && row === 1;
                    return (
                      <span
                        key={`${column}-${row}`}
                        className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full"
                        style={{
                          left: pos.x * 0.75,
                          top: pos.y * 0.75,
                          width: isOrigin ? 7 : 3,
                          height: isOrigin ? 7 : 3,
                          background: isOrigin ? "#ef4444" : isX ? "#22c55e" : isY ? "#3b82f6" : "rgba(255,255,255,0.5)",
                        }}
                        title={`cell c${column} r${row} -> view (${pos.x}, ${pos.y})`}
                      />
                    );
                  }),
                )}
                <span className="absolute bottom-0 left-0 bg-black/70 px-1 text-[8px] text-cyan-200">
                  native 240x240 view space · basis ({CELL_BASIS.xStep.x},{CELL_BASIS.xStep.y})/(
                  {CELL_BASIS.yStep.x},{CELL_BASIS.yStep.y}) · cam x{rowOffset}
                </span>
              </div>
            )}

            {/*
              The HUD layer is asset-only evidence: `battle/member_info_bar`, `treasure_bar`,
              `command_bar`, the item slots and `fukidashi/bubble` exist in the original archive, but
              which bars a fight shows, where they sit and what they read is not recovered - the item
              boxes and their counts came from the supplied footage. Lab surface only.
            */}
            {labMode && showHud && (
              <>
                <img
                  src={HUD_SPRITES.memberInfoBar}
                  alt="member info bar"
                  className="absolute left-1 top-1 w-32 opacity-95"
                  style={{ imageRendering: "pixelated" }}
                />
                <div className="absolute left-2 top-2 text-[9px] leading-tight text-white">
                  <div>Average Lv.</div>
                  <div>Members</div>
                </div>
                <div className="absolute left-20 top-2 text-[9px] leading-tight text-white">
                  <div>{encounter.level}</div>
                  <div>
                    {Math.max(1, encounter.enemies.length - 4)}/{encounter.enemies.length}
                  </div>
                </div>
                <img
                  src={HUD_SPRITES.treasureBar}
                  alt="treasure bar"
                  className="absolute right-1 top-1 w-32 opacity-90"
                  style={{ imageRendering: "pixelated" }}
                />
                <div className="absolute bottom-6 right-2 text-right text-[9px] leading-tight text-white">
                  <div>Members</div>
                  <div>
                    {encounter.allies.length}/{encounter.allies.length + 4}
                  </div>
                </div>
                <img
                  src={HUD_SPRITES.commandBar}
                  alt="command bar"
                  className="absolute bottom-0 left-1/2 w-40 -translate-x-1/2 opacity-90"
                  style={{ imageRendering: "pixelated" }}
                />
                <div className="absolute bottom-1 left-2 flex gap-1">
                  {[
                    { icon: "🧪", count: 87 },
                    { icon: "🌿", count: 17 },
                  ].map((slot) => (
                    <div key={slot.icon} className="relative">
                      <img
                        src={HUD_SPRITES.itemSlot}
                        alt=""
                        className="h-7 w-7"
                        style={{ imageRendering: "pixelated" }}
                      />
                      <span className="absolute inset-0 flex items-center justify-center text-[10px]">
                        {slot.icon}
                      </span>
                      <span className="absolute -bottom-0.5 right-0 rounded bg-black/70 px-0.5 text-[8px] text-white">
                        ×{slot.count}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {labMode && showBubble && (
              <div className="absolute left-2 bottom-12">
                <img
                  src={HUD_SPRITES.bubble}
                  alt=""
                  className="h-6 w-auto"
                  style={{ imageRendering: "pixelated" }}
                />
                <div className="mt-0.5 max-w-[220px] rounded bg-black/70 px-2 py-1 text-[10px] text-white">
                  {event.side === "enemy" ? bossName : "Ally"}: step {step + 1}/{encounter.timeline.length}
                </div>
              </div>
            )}
          </BattleStage>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => setPlaying((value) => !value)}>
              {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              {playing ? "Pause" : "Play"}
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setStep((s) => Math.max(0, s - 1))}>
              <ChevronLeft className="w-4 h-4" /> Prev
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setStep((s) => Math.min(encounter.timeline.length - 1, s + 1))}
            >
              Next <ChevronRight className="w-4 h-4" />
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={resetReplay}>
              <RotateCcw className="w-4 h-4" /> Reset
            </Button>
            <span className="text-xs text-muted-foreground">
              event {step + 1} / {encounter.timeline.length}
            </span>
          </div>

          {labMode && (
            <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-[11px]">
            <EvidenceBadge level={BATTLE_REGISTRATION_EVIDENCE.evidence} />
            <span className="text-muted-foreground">
              Battle grid registration: CONFIRMED so far: the battle view rect (
              {BATTLE_VIEW.width}x{BATTLE_VIEW.height}), the cell basis (+24,+12) / (-24,+12), the
              start camera (BattleSystem.Start 0x14ed108) and zoom 1.0, so the debug grid is drawn in
              native view pixels on the arena. Still open: the backdrop's own draw call is replaced by
              the recovered `backgroundX` placement, and the runtime visible width is a platform value
              (see the view profile above). {BATTLE_REGISTRATION_EVIDENCE.note}
            </span>
            </div>
          )}

          {labMode && (
          <p className="text-[11px] text-muted-foreground">
            Facing (native): <code>Direction</code> is UP 0 / RIGHT 1 / DOWN 2 / LEFT 3 and the clip
            is <code>groupBase + direction</code> (<code>GetBehaviorBySebId</code> clears the low
            two bits). <code>monster/seb.inf</code> lists the four variants per group as{" "}
            <code>_up</code>, <code>_right</code>, <code>_right,u</code>, <code>_up,u</code>, so DOWN
            and LEFT reuse the RIGHT/UP assets with the <code>,u</code> load flag instead of having
            their own SEBs. <code>FighterSystem.InitFighters</code> stores{" "}
            <code>Invert(slot direction)</code>, i.e. allies face UP (no flag) and enemies face
            DOWN (<code>_right,u</code>). The flag becomes a mirror through <code>Seb.Flip</code>:
            GetSprite flips the sprite's reversU, <code>DrawZSortObjects</code> maps reversU/reversV
            to the Graphics mirror mode, and DrawImage turns bit 0 into scaleX = -1 about the
            destination rect centre — so the flip covers every SEB line of the unit and moves
            neither the line origin nor the OPT destination.
          </p>
          )}

          {labMode && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[
              { label: "Effect layer", checked: showEffects, set: setShowEffects },
              { label: "Target arrows", checked: showArrows, set: setShowArrows },
              { label: "Skill balloon", checked: showBalloon, set: setShowBalloon },
              { label: "HUD bars", checked: showHud, set: setShowHud },
              { label: "Dialog bubble", checked: showBubble, set: setShowBubble },
              { label: "Full enemy roster (21)", checked: fullRoster, set: setFullRoster },
              { label: "Unit name / cell labels", checked: showUnitLabels, set: setShowUnitLabels },
              { label: "Occupancy / reflow (proven rules)", checked: dynamics, set: setDynamics },
              { label: "Debug: slot / cell / index", checked: debugFormation, set: setDebugFormation },
              { label: "Debug: SEB frame / line selection", checked: debugSeb, set: setDebugSeb },
              { label: "Debug: battle grid (8x16 lattice)", checked: debugGrid, set: setDebugGrid },
              { label: "Recovered native battle view", checked: nativeMode, set: setNativeMode },
              { label: "Frozen human idle (Guard D / Archer C)", checked: humanIdle, set: setHumanIdle },
            ].map((toggle) => (
              <div key={toggle.label} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                <Label className="text-xs">{toggle.label}</Label>
                <Switch checked={toggle.checked} onCheckedChange={toggle.set} />
              </div>
            ))}
          </div>
          )}

          {!labMode && (
            <>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => setLabMode(true)}>
              Show lab controls
            </Button>
            <span className="text-[11px] text-muted-foreground">
              Recovered Replay · {encounter.title} · arena {arena.id} · {viewProfile.label} ·{" "}
              {liveCounts.ally}/{encounter.allies.length} allies and {liveCounts.enemy}/
              {encounter.enemies.length} enemies on the field at event {step + 1} of{" "}
              {encounter.timeline.length}
            </span>
          </div>
            </>
          )}

          {labMode && (
          <p className="text-[11px] text-muted-foreground">
            The defaults above are the faithful set: recovered native view, frozen native human idle,
            effects, skill balloon, HUD bars and the full 21-enemy roster, with every debug overlay off.
            The historical spread layout, the legacy human renderers, the APPROX runtime-line overlay
            and the non-native "right" facing were removed from the panel - the URL flags{" "}
            <code>?legacy-layout=1</code>, <code>?human-idle=0</code>, <code>?runtime-parts=1</code> and{" "}
            <code>?facing-right=1</code> still reproduce them for comparisons. The default view profile
            is the recording-supported ~240 logical window, which clips whatever falls outside logical
            X 0..{viewProfile.logicalViewWidth} instead of moving fighters inward; pick the 481-wide
            profile to see the entire field at once.
          </p>
          )}
        </CardContent>
      </Card>

      {/*
        Lab surface only: every evidence card below documents the recovery and the experimental
        switches. Recovered Replay shows just the stage and this page's locked configuration.
      */}
      {labMode && (
        <>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Human image state (recovered native model)</CardTitle>
          <CardDescription>
            What the original battle sets up for a human fighter before anything is drawn: a 17-slot{" "}
            <code>int[] imgIds</code> on <code>ecs.HumanComponent</code>, seeded from the job/appearance
            record and the two equipped item records. The two frozen demonstration configurations below
            are the only units whose slots reach the stage, through the EQUIP_WAIT frame-0 line data;
            every other character keeps the comparison renderers.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-xs">
          <div className="flex items-start gap-2 rounded-md border px-3 py-2">
            <EvidenceBadge level={HUMAN_IMAGE_MODEL_EVIDENCE.evidence} />
            <span className="text-muted-foreground">{HUMAN_IMAGE_MODEL_EVIDENCE.note}</span>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">idx</TableHead>
                <TableHead className="w-32">IMG_*</TableHead>
                <TableHead>recovered creation rule</TableHead>
                <TableHead className="w-24">evidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {HUMAN_IMAGE_SLOTS.map((slot) => (
                <TableRow key={slot.index}>
                  <TableCell className="font-mono">{slot.index}</TableCell>
                  <TableCell className="font-mono">{slot.kind ?? "—"}</TableCell>
                  <TableCell>
                    <span className="font-mono">{slot.rule}</span>
                    {slot.note && (
                      <span className="block text-[10px] text-muted-foreground">{slot.note}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <EvidenceBadge level={slot.evidence} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="space-y-1 rounded-md border px-3 py-2">
            {HUMAN_IMAGE_INPUTS.map((input) => (
              <div key={input.name} className="flex items-start gap-2">
                <EvidenceBadge level={input.evidence} />
                <span className="text-muted-foreground">
                  <span className="font-mono text-foreground">{input.name}</span> — {input.detail}
                </span>
              </div>
            ))}
          </div>
          <div className="space-y-1 rounded-md border px-3 py-2">
            {HUMAN_IMAGE_PIPELINE.map((step) => (
              <div key={step.step} className="flex items-start gap-2">
                <EvidenceBadge level={step.evidence} />
                <span className="text-muted-foreground">{step.detail}</span>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-muted-foreground">
            This replay's allies ({encounter.allies.length}) carry only website labels —{" "}
            {encounter.allies
              .map((unit) => `${unit.jobName ?? unit.label ?? "ally"} ${unit.rank ?? ""}`.trim())
              .join(", ")}{" "}
            — with no native job row, gender or equipment of their own. A slot value is therefore
            applied only where a frozen configuration exists (the two demonstration inputs above);
            every other unit keeps a comparison renderer.
          </p>
          {/*
            The frozen idle path: the one place where the 17 slots above reach the stage. Both
            configurations are demonstration inputs (the job record's first per-gender appearance
            entry plus the job's default weapon/shield and the battle-time rewrites), not a
            reproduction of any saved character.
          */}
          <div className="space-y-1 rounded-md border px-3 py-2">
            <div className="flex items-start gap-2">
              <EvidenceBadge level="SUPPORTED" />
              <span className="text-muted-foreground">
                <span className="font-mono text-foreground">frozen idle</span> —{" "}
                {HUMAN_IDLE_STATE.clip} / {HUMAN_IDLE_STATE.imageSetRow} / direction{" "}
                {HUMAN_IDLE_STATE.direction} / SEB frame {HUMAN_IDLE_STATE.frame} of{" "}
                <span className="font-mono">
                  {HUMAN_IDLE_STATE.seb.group}/{HUMAN_IDLE_STATE.seb.file}
                </span>{" "}
                ({HUMAN_IDLE_STATE.seb.bytes} bytes, {HUMAN_IDLE_STATE.seb.keyRecords} key records,{" "}
                {HUMAN_IDLE_STATE.seb.layers} lines). {HUMAN_IDLE_RESOLUTION.note}
              </span>
            </div>
            {HUMAN_IDLE_CHARACTERS.map((character) => (
              <div key={character.id} className="flex items-start gap-2">
                <EvidenceBadge level="SUPPORTED" />
                <span className="text-muted-foreground">
                  <span className="font-mono text-foreground">
                    {character.label} (job {character.jobSourceId}, gender {character.genderIndex},
                    flag {character.flag})
                  </span>{" "}
                  imgIds [{character.imgIds.join(", ")}] → {character.expectedDraws} drawn lines (
                  {character.expectedLines.map((line) => `L${line}`).join(", ")})
                </span>
              </div>
            ))}
            <div className="flex items-start gap-2">
              <EvidenceBadge level={HUMAN_IDLE_GROUP.partDirsEvidence} />
              <span className="text-muted-foreground">
                line → group row [{HUMAN_IDLE_GROUP.row.join(", ")}]; drawn groups resolve to{" "}
                {Object.entries(HUMAN_IDLE_GROUP.partDirs)
                  .map(([res, dir]) => `${res}→${dir}`)
                  .join(", ")}. {HUMAN_IDLE_GROUP.partDirsNote}
              </span>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Native body renderer (recovered)</CardTitle>
          <CardDescription>
            One z-sort entry per SEB line, drawn in ascending line order with the record's own crop,
            offset and flip flags; size = the record's w/h unless the fighter carries
            ModifyAnimationComponent.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-xs">
          <p className="font-mono">{BODY_PLACEMENT_EQUATION}</p>
          <ul className="space-y-1">
            <li>
              <EvidenceBadge level={VIEW_ORIGIN.evidence} /> view origin {VIEW_ORIGIN.viewX},
              {VIEW_ORIGIN.viewY} — {VIEW_ORIGIN.note}
            </li>
            <li>
              <EvidenceBadge level={MODIFY_ANIMATION.evidence} /> scaling — {MODIFY_ANIMATION.note}
            </li>
            <li>
              <EvidenceBadge level="CONFIRMED" /> monsters use two lines (shadow + body); family
              small = {MONSTER_SEB_FAMILIES.small.seb} ({MONSTER_SEB_FAMILIES.small.cellW}x
              {MONSTER_SEB_FAMILIES.small.cellH} cells), xl = {MONSTER_SEB_FAMILIES.xl.seb} (
              {MONSTER_SEB_FAMILIES.xl.cellW}x{MONSTER_SEB_FAMILIES.xl.cellH} body cells). Creatures
              differ in drawn size because their own images differ, not from a universal scale.
            </li>
            <li>
              <EvidenceBadge level="UNKNOWN" /> human lines 7-11 (hair, hat, accessory, eye, mouth)
              carry texId -1 with a zero placeholder in equip_wait_up.seb; the game fills them from
              the fighter's ImageComponent (Entity.AddImage 0x146F2A4, called from World.CreateHuman
              0x1476854) whose values live in the job/dress tables, so the frozen idle path draws no
              layer for them. The composite comparison renderer's overlay toggle draws them aligned to
              the face cell and is labelled APPROX.
            </li>
            <li>
              <EvidenceBadge level={SEB_SPRITE_SELECTION_RULE.evidence} /> per-line crop selection —{" "}
              <code>{SEB_SPRITE_SELECTION_RULE.method}</code>: {SEB_SPRITE_SELECTION_RULE.pseudocode}.
              {" "}
              {SEB_SPRITE_SELECTION_RULE.note}
            </li>
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Formation geometry provenance</CardTitle>
          <CardDescription>
            Where each placement rule comes from. `direct` means the value was read from the named
            original function; the only fitted value is the stage scale.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted/30 text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 text-left font-medium">Rule</th>
                  <th className="px-2 py-2 text-left font-medium">Value</th>
                  <th className="px-2 py-2 text-left font-medium">Source</th>
                  <th className="px-2 py-2 text-left font-medium">Direct</th>
                  <th className="px-2 py-2 text-left font-medium">Tag</th>
                </tr>
              </thead>
              <tbody>
                {FORMATION_RULES.map((rule) => (
                  <tr key={rule.id} className="border-t border-border/60">
                    <td className="px-2 py-2 font-mono">{rule.id}</td>
                    <td className="px-2 py-2">
                      <div>{rule.value}</div>
                      <div className="text-[10px] text-muted-foreground">{rule.note}</div>
                    </td>
                    <td className="px-2 py-2 font-mono text-[10px]">{rule.source}</td>
                    <td className="px-2 py-2 font-mono">{rule.direct ? "yes" : "no"}</td>
                    <td className="px-2 py-2"><EvidenceBadge level={rule.evidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] text-muted-foreground">
            Cell size {FORMATION_CONSTANTS.cellSize}, {FORMATION_CONSTANTS.columns} columns, field{" "}
            {FORMATION_CONSTANTS.mapWidth}x{FORMATION_CONSTANTS.mapHeight}. The two front rows are
            adjacent cells, so the teams meet at one cell of separation.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Fighter animation states (recovered)</CardTitle>
          <CardDescription>
            Behaviour id passed to <code>AnimationSet.ChangeAnimation</code> per fighter state, with
            the human and monster animation groups it selects. Clip id = base + direction, so the
            four directions come from one table (monsters reuse bodies with a mirror flag).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted/30 text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 text-left font-medium">State</th>
                  <th className="px-2 py-2 text-left font-medium">Behaviour</th>
                  <th className="px-2 py-2 text-left font-medium">Human base / SEB</th>
                  <th className="px-2 py-2 text-left font-medium">Monster base / SEB</th>
                  <th className="px-2 py-2 text-left font-medium">Source</th>
                  <th className="px-2 py-2 text-left font-medium">Tag</th>
                </tr>
              </thead>
              <tbody>
                {ANIMATION_STATE_RULES.map((rule) => (
                  <tr key={rule.id} className="border-t border-border/60">
                    <td className="px-2 py-2 font-mono">{rule.id}</td>
                    <td className="px-2 py-2 font-mono">{String(rule.behavior)}</td>
                    <td className="px-2 py-2">
                      <div className="font-mono">{rule.humanBase ?? "-"}</div>
                      <div className="text-[10px] text-muted-foreground">{rule.humanSeb ?? "-"}</div>
                    </td>
                    <td className="px-2 py-2">
                      <div className="font-mono">{rule.monsterBase ?? "-"}</div>
                      <div className="text-[10px] text-muted-foreground">{rule.monsterSeb ?? "-"}</div>
                    </td>
                    <td className="px-2 py-2 font-mono text-[10px]">{rule.source}</td>
                    <td className="px-2 py-2"><EvidenceBadge level={rule.evidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] text-muted-foreground">
            Decoded monster clips ({Object.values(MONSTER_CLIPS).map((clip) => `${clip.id}: ${clip.frames.length} keys / maxFrame ${clip.maxFrame}`).join(" · ")}) come
            from the original SEB files via <code>tools/recovery/export_battle_animation.py</code>.
            Frame advance is the recovered native one: {NATIVE_ANIMATION.evidence} —
            {" "}
            <code>AnimationSystem.Update 0x14DC884</code> adds <code>AnimationComponent.rate</code> to
            the unit's <code>frame</code> once per rendered frame; monsters are created with{" "}
            <code>rate {NATIVE_ANIMATION.monsterRate}</code> (<code>World.CreateMonster 0x1477A68</code>)
            and the game targets <code>{NATIVE_ANIMATION.targetFps} fps</code> (
            <code>Application.targetFrameRate</code> from <code>FormManager.tagFps_</code>), so one SEB
            frame takes <code>{NATIVE_ANIMATION.frameMs} ms</code>. Clip lengths are the original
            maxFrame values, and each unit restarts at frame 0 when its animation changes
            (<code>ChangeAnimation 0x165DEA8</code>). {NATIVE_ANIMATION.note}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Formation dynamics provenance</CardTitle>
          <CardDescription>
            Occupancy, advancement and reflow rules. Enable “Occupancy / reflow” to drive the stage
            from these rules and “Debug” to print each unit's original slot, current cell and member
            index.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted/30 text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 text-left font-medium">Rule</th>
                  <th className="px-2 py-2 text-left font-medium">Behaviour</th>
                  <th className="px-2 py-2 text-left font-medium">Source</th>
                  <th className="px-2 py-2 text-left font-medium">Tag</th>
                </tr>
              </thead>
              <tbody>
                {DYNAMIC_RULES.map((rule) => (
                  <tr key={rule.id} className="border-t border-border/60">
                    <td className="px-2 py-2 font-mono">{rule.id}</td>
                    <td className="px-2 py-2">
                      <div>{rule.value}</div>
                      <div className="text-[10px] text-muted-foreground">{rule.note}</div>
                    </td>
                    <td className="px-2 py-2 font-mono text-[10px]">{rule.source}</td>
                    <td className="px-2 py-2"><EvidenceBadge level={rule.evidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
            <span>beat {beatState.beat}/{beats.length - 1}</span>
            <span>free cells: {beatState.free.length}</span>
            <span>
              KO'd: {beatState.units.filter((unit) => !unit.alive).length} · moved this beat:{" "}
              {beatState.units.filter((unit) => unit.movedThisBeat).length}
            </span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Timeline</CardTitle>
          <CardDescription>
            Example beats per encounter: skill triggers, damage, misses, defensive reactions and
            status labels. Amounts are illustrative; the overlay set is what is being shown.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            {encounter.timeline.map((entry, index) => (
              <button
                key={`${entry.kind}-${index}`}
                type="button"
                onClick={() => {
                  setStep(index);
                  setPlaying(false);
                }}
                className={`rounded-md border px-2 py-1 text-[11px] ${
                  index === step ? "border-primary bg-primary/10" : "border-border"
                }`}
              >
                {index + 1}. {entry.side} {entry.kind}
                {entry.skillId ? ` #${entry.skillId}` : ""}
                {entry.amount !== undefined ? ` ${entry.amount}` : ""}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Render stack</CardTitle>
          <CardDescription>Draw order used by the shared renderer, bottom first.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {BATTLE_LAYERS.map((layer, index) => (
            <div key={layer.id} className="flex flex-col gap-1 rounded-md border px-3 py-2 sm:flex-row sm:items-start sm:gap-3">
              <div className="flex min-w-[190px] items-center gap-2">
                <span className="font-mono text-[10px] text-muted-foreground">{index + 1}</span>
                <span className="text-xs font-medium">{layer.label}</span>
                <EvidenceBadge level={layer.evidence} />
              </div>
              <span className="text-[11px] text-muted-foreground">{layer.note}</span>
            </div>
          ))}
          <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-[11px]">
            <EvidenceBadge level={SCREEN_MAPPING_EVIDENCE.evidence} />
            <span className="text-muted-foreground">
              Slot mapping: {SCREEN_MAPPING_EVIDENCE.note} Each unit shows its recovered native cell
              (c = column, r = row) next to its name.
            </span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Recovered combat-effect calls used by the overlays</CardTitle>
          <CardDescription>
            Arguments read from the original code paths; these drive which asset each overlay uses.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted/30 text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 text-left font-medium">Effect</th>
                  <th className="px-2 py-2 text-left font-medium">type</th>
                  <th className="px-2 py-2 text-left font-medium">value1</th>
                  <th className="px-2 py-2 text-left font-medium">value2</th>
                  <th className="px-2 py-2 text-left font-medium">resource</th>
                  <th className="px-2 py-2 text-left font-medium">seb</th>
                  <th className="px-2 py-2 text-left font-medium">Y offset</th>
                  <th className="px-2 py-2 text-left font-medium">lifetime</th>
                  <th className="px-2 py-2 text-left font-medium">tag</th>
                </tr>
              </thead>
              <tbody>
                {NATIVE_EFFECT_SPECS.map((spec) => (
                  <tr key={spec.name} className="border-t border-border/60">
                    <td className="px-2 py-2">
                      <div className="font-medium">{spec.name}</div>
                      <div className="text-[10px] text-muted-foreground">{spec.note}</div>
                    </td>
                    <td className="px-2 py-2 font-mono">{spec.type}</td>
                    <td className="px-2 py-2 font-mono">{spec.value1}</td>
                    <td className="px-2 py-2 font-mono">{spec.value2}</td>
                    <td className="px-2 py-2 font-mono">{spec.resource}</td>
                    <td className="px-2 py-2 font-mono">{spec.seb}</td>
                    <td className="px-2 py-2 font-mono">{spec.offsetY}</td>
                    <td className="px-2 py-2 font-mono">{spec.lifetime}</td>
                    <td className="px-2 py-2"><EvidenceBadge level={spec.evidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
        </>
      )}
    </div>
  );
}
