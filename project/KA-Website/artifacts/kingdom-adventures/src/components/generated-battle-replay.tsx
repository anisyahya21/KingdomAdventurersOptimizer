import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "wouter";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  GENERATED_BATTLE_STORE_VERSION,
  readGeneratedBattle,
  type GeneratedBattleRecord,
} from "@/lib/generated-battle-store";
import {
  buildGeneratedFrames,
  buildGeneratedUnits,
  explainGeneratedEvent,
  generatedAttackClip,
  generatedCoverage,
  generatedEventGroup,
  generatedFinishOutcome,
  generatedHumanCharacter,
  generatedFighterVitals,
  generatedFrameClipId,
  generatedHumanLinesForFrame,
  generatedMonsterClip,
  generatedRewardSummary,
  generatedScenePlacement,
  generatedSkillEvents,
  generatedStageMode,
  generatedTickSummary,
  generatedVerdictWord,
  generatedViewPanX,
  loadGeneratedHumanRules,
  type GeneratedHumanRules,
  type GeneratedUnitFrame,
  type GeneratedUnitView,
} from "@/lib/generated-battle-view";
import {
  CONSUMABLE_UNSUPPORTED_TEXT,
  GENERATED_IMPACT_LIFETIME_TICKS,
  buildGeneratedStageOverlays,
  generatedChestDrops,
  generatedChestSummary,
  generatedChestsAt,
  generatedConsumableRows,
  generatedEquippedPreview,
  generatedFrameIndexForTick,
  generatedImpactFrame,
  generatedRenderTally,
  generatedReleaseInvocation,
  generatedSkillReleases,
  type GeneratedConsumableRow,
  type GeneratedStageOverlay,
} from "@/lib/generated-battle-visuals";
import {
  BATTLE_INTERACTION_POLL_SCHEMA,
  BATTLE_INTERACTION_WINDOW_TICKS,
  buildBattleInteractionRequest,
  createHttpBattleInteractionTransport,
  createHttpBattleInteractionPollTransport,
  interactionFromTick,
  interactionScenarioFromRecord,
  mergeBranchExtension,
  planConsumableClick,
  preservedPrefixLength,
  requestBattleBranchPoll,
  requestBattleInteraction,
  type BattleInteractionResult,
} from "@/lib/battle-interaction";
import { BattleStage, CombatText, DamageNumber, LoadoutStageSprite, UnitSprite } from "@/components/battle-stage";
import { NativeFighterGauges } from "@/components/native-fighter-gauges";
import {
  BattleAllyVitals,
  BattleTreasureStrip,
  BattleVerdictPopup,
  ConsumableActionBar,
  type AllyVitalRow,
} from "@/components/ka/battle-hud";
import { invocationLabel } from "@/lib/battle-team-draft";
import {
  ARENAS,
  BATTLE_DIRECTIONS,
  DEFAULT_VIEW_PROFILE_ID,
  EFFECT_SPRITES,
  INDICATOR_SPRITES,
  MONSTER_DIRECTION_VARIANTS,
  SHARED_ARENA_ID,
  SKILL_BALLOON_FRAMES,
  SKILL_BALLOON_SHEET,
  backgroundXForViewWidth,
  monsterSpriteById,
  nativeInitialFormation,
  verticalPresentationRatio,
  viewProfileById,
  type BattleUnit,
} from "@/lib/battle-replay";
import { battlePlaybackEndTick, parseBattleReplayResult, type BattleReplayEvent, type BattleReplayResult } from "@/lib/battle-replay-result";
import { advanceBrowserBattle, browserBattleActive, startBrowserBattle, useBrowserBattleItem } from "@/lib/browser-battle";
import type { FighterVitals } from "@/lib/native-fighter-gauges";
import { downloadJson } from "@/lib/download-json";
import { optimizerFightExport, optimizerScenarioFilename } from "@/lib/strategy-optimizer-export";
import type { CombatScenario } from "@/lib/battle-setup-adapter";

/**
 * GENERATED REPLAY - product/visual pass, 2026-09-20.
 *
 * The page leads with the FIGHT a player asked for:
 *  * every fighter on the stage is drawn from its own recovered art - a monster from its SEB sheet, a
 *    human from the recovered native clip when the builder shipped raw `appearanceInputs`, and
 *    otherwise from the built-in loadout renderer in the fighter's own job/rank/gender plus equipped
 *    weapon and shield (BUILDER-CONTRACT.md section 2);
 *  * the runner's own events are painted on the stage as they happen: attacker-to-target arrow, hit
 *    impact, recovered damage number (white/critical/red rows), Missed / Critical Hit text, healing
 *    numbers, and the skill balloon with the canonical skill icon and name when a `release` record
 *    with `used = true` fires (the recovered pay-MP + CreateSkillBalloon path);
 *  * the chest counter ticks up while the fight plays, from the runner's own `prize` events, and the
 *    chest list shows each drop's real treasure sprite;
 *  * consumables are clickable: a click branches the SAME scenario at the displayed tick through the
 *    interaction contract (`ka-battle-interaction-1`), and the returned replay is what then plays -
 *    never a client-painted effect.
 *
 * Honesty this page keeps:
 *  * a queued chest is provisional; the runner's certified `awardedChestCount` is an entitlement, not
 *    collected inventory (the chest card says both);
 *  * a row is highlighted as "usable now" only from the recovered usable condition (supported branch,
 *    stock left at this tick, an eligible own-team fighter below that parameter's maximum) - never as
 *    the best moment to click;
 *  * a fighter whose art or appearance is genuinely absent keeps a labelled placeholder instead of an
 *    invented stand-in animation.
 *
 * Everything numeric stays the replay's own: this module re-simulates nothing and derives no verdict.
 */

/** Param.GetRate 0x165E91C: nonpositive max -> 0; otherwise floor(current*100/max), clamped 0..100. */
function nativeGaugePercent(current: number, maximum: number): number {
  if (maximum <= 0) return 0;
  const rate = Math.trunc((current * 100) / maximum);
  if (rate <= 0) return current > 0 ? 1 : 0;
  return Math.min(100, rate);
}

/** The recovered 18x3 mini_gauge with its 15x1 fill, drawn at DOM scale here. */
function NativeGauge({ label, current, maximum }: { label: string; current: number; maximum: number }) {
  const percent = nativeGaugePercent(current, maximum);
  const fill = percent === 0 ? 0 : Math.max(1, Math.trunc((15 * percent) / 100));
  return (
    <div className="flex items-center gap-2 font-mono text-[10px]" data-gauge={label}>
      <span className="w-5 shrink-0 text-muted-foreground">{label}</span>
      <span className="relative inline-block h-[3px] w-[18px] bg-black/60" data-gauge-track>
        <span
          className="absolute left-[1px] top-[1px] block h-[1px] bg-emerald-400"
          style={{ width: `${fill}px` }}
          data-gauge-fill={fill}
        />
      </span>
      <span data-gauge-value={`${current}/${maximum}`}>
        {current}/{maximum}
      </span>
    </div>
  );
}

function formatUnknown(value: unknown): string {
  if (value === null || value === undefined) return "none reported";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function UnitRow({ unit, frame }: { unit: GeneratedUnitView; frame: GeneratedUnitFrame | undefined }) {
  const hp = frame?.hp ?? unit.startHp;
  const mp = frame?.mp ?? unit.startMp;
  const maximumHp = frame?.maxHp ?? unit.maxHp;
  const maximumMp = frame?.maxMp ?? unit.maxMp;
  const clip = generatedAttackClip(unit);
  const visual = frame?.visual ?? "waiting";
  const dead = frame?.dead ?? false;
  const leaving = frame?.leaving ?? false;
  return (
    <div className="rounded-md border border-border/60 p-2 text-xs" data-generated-unit={unit.unitId}
      data-generated-unit-side={unit.side}
      data-generated-unit-kind={unit.kind}
      data-generated-unit-cell={(frame?.cell ?? unit.initialCell).join(",")}
      data-generated-unit-hp={hp}
      data-generated-unit-max-hp={maximumHp ?? "unknown"}
      data-generated-unit-state={frame?.state ?? 0}
      data-generated-unit-visual={visual}
      data-generated-unit-dead={dead ? "1" : "0"}
      data-generated-unit-leaving={leaving ? "1" : "0"}
      data-generated-unit-clip={frame?.clipId ?? ""}
      data-generated-unit-status={frame?.status.join("|") ?? ""}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{unit.name}</span>
        <Badge variant="outline" className="font-mono text-[10px]">
          {unit.side}:{unit.rosterIndex}
        </Badge>
        <span className="text-muted-foreground">
          {unit.human ? `${unit.jobId ?? "human"} ${unit.rank ?? ""}`.trim() : `monster ${unit.monsterId}`}
        </span>
        <span className="text-muted-foreground">cell [{frame?.cell?.join(",") ?? unit.initialCell.join(",")}]</span>
        {unit.weaponMotion !== null ? (
          <span className="text-muted-foreground" data-unit-weapon-motion={unit.weaponMotion}>
            weapon {unit.weaponId} · motion {unit.weaponMotion}
            {clip === null ? " (clip not decoded yet - PARTIAL)" : ` · attack clip base ${clip}`}
          </span>
        ) : null}
        <Badge variant={unit.animationCoverage === "FULL" ? "secondary" : "outline"} className="text-[10px]">
          animation {unit.animationCoverage}
        </Badge>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        {maximumHp === null ? <span data-gauge="HP">HP {hp}/?</span> : <NativeGauge label="HP" current={hp} maximum={maximumHp} />}
        {maximumMp === null ? <span data-gauge="MP">MP {mp}/?</span> : <NativeGauge label="MP" current={mp} maximum={maximumMp} />}
        <span className="font-mono text-[10px] text-muted-foreground" data-unit-visual-state>
          state {frame?.state ?? 0}/{frame?.stateName ?? "-"} → {visual}
          {dead ? " · DEAD (event HP 0)" : ""}
          {leaving ? " · LEAVING (state 8)" : ""}
        </span>
        {frame?.clipId ? (
          <span className="font-mono text-[10px] text-muted-foreground" data-unit-clip>
            clip {frame.clipId} f{frame.clipFrame}
            {frame.clipBehavior === null ? "" : ` · behaviour ${frame.clipBehavior}`}
          </span>
        ) : null}
      </div>
      {frame?.status.length ? (
        <div className="mt-1 flex flex-wrap gap-1" data-unit-status-tags>
          {frame.status.map((tag) => (
            <Badge key={tag} variant="outline" className="font-mono text-[9px]" data-unit-status-tag={tag}>
              {tag}
            </Badge>
          ))}
        </div>
      ) : null}
      {unit.skillIds.length ? (
        <div className="mt-1 font-mono text-[10px] text-muted-foreground" data-unit-skills>
          skills {unit.skillIds.map((id, index) => `${id}@${unit.invocationLevels[index] ?? 0}`).join(" ")}
        </div>
      ) : null}
    </div>
  );
}

/** the recovered balloon art is drawn at 1.35x in the native-sized scene (80x24 -> 108x32) */
const SKILL_BALLOON_SCALE = 1.35;

/** `skill_balloon.png` is two stacked 80x24 frames (ally orange at y=0, enemy blue at y=24) */
const SKILL_BALLOON_FRAME_COUNT = 2;

/* ------------------------------------------------------------------ */
/* Stage overlays                                                      */
/* ------------------------------------------------------------------ */

function anchorNumber(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * The event layer on the battlefield: the recovered arrow / impact / number / status / skill-balloon
 * art, drawn in the same scene coordinates as the fighters so one presentation transform scales all of
 * it together. Every item comes from `buildGeneratedStageOverlays` (runner events only); the skill
 * icon and name come from the canonical skill catalog.
 */
function StageOverlayLayer({
  items,
  units,
}: {
  items: GeneratedStageOverlay[];
  /** the replay's own units, so a release can resolve its declared invocation level */
  units: GeneratedUnitView[];
}) {
  return (
    <>
      {items.map((item) => {
        if (item.kind === "arrow") {
          const left = (anchorNumber(item.from.left) + anchorNumber(item.to.left)) / 2;
          const top = (anchorNumber(item.from.top) + anchorNumber(item.to.top)) / 2;
          return (
            <img
              key={item.key}
              src={INDICATOR_SPRITES.arrow}
              alt=""
              className="pointer-events-none absolute"
              style={{
                left: `${left}px`,
                top: `${top}px`,
                transform: `translate(-50%, -50%)${item.flipped ? " scaleX(-1)" : ""}`,
                imageRendering: "pixelated",
                opacity: 0.8,
              }}
            />
          );
        }
        if (item.kind === "impact") {
          /*
           * One recovered animation frame, not the whole 114x35 optimizer pack: the crop is clipped
           * out of the sheet with `background-position` and centred on the target at the recovered
           * SEB translation plus this cell's own OPT offset.
           */
          const frame = generatedImpactFrame(item.frame);
          return (
            <span
              key={item.key}
              role="presentation"
              className="pointer-events-none absolute"
              style={{
                left: `calc(${item.anchor.left} + ${frame.centerX}px)`,
                top: `calc(${item.anchor.top} + ${frame.centerY}px)`,
                width: frame.width,
                height: frame.height,
                backgroundImage: `url(${EFFECT_SPRITES.impact.file})`,
                backgroundPosition: `-${frame.sourceX}px -${frame.sourceY}px`,
                imageRendering: "pixelated",
                transform: `translate(-50%, -50%) scale(${item.scale})`,
              }}
            />
          );
        }
        if (item.kind === "combat-text") {
          return (
            <div
              key={item.key}
              className="pointer-events-none absolute"
              style={{ left: item.anchor.left, top: `calc(${item.anchor.top} - 18px)`, transform: "translate(-50%, -100%)" }}
            >
              <span style={{ display: "inline-block", transform: "scale(1.8)", transformOrigin: "bottom center" }}>
                <CombatText label={item.label} />
              </span>
            </div>
          );
        }
        if (item.kind === "damage") {
          return (
            <div
              key={item.key}
              className="pointer-events-none absolute"
              style={{ left: item.anchor.left, top: `calc(${item.anchor.top} - 18px)`, transform: "translate(-50%, -100%)" }}
            >
              <span style={{ display: "inline-block", transform: "scale(1.8)", transformOrigin: "bottom center" }}>
                <DamageNumber amount={item.amount} style={item.style} />
              </span>
            </div>
          );
        }
        if (item.kind === "heal") {
          return (
            <div
              key={item.key}
              className="pointer-events-none absolute"
              style={{ left: item.anchor.left, top: `calc(${item.anchor.top} - 30px)`, transform: "translate(-50%, 0)" }}
            >
              <span style={{ display: "inline-block", transform: "scale(1.6)" }}>
                <DamageNumber amount={item.amount} style="ally" />
              </span>
            </div>
          );
        }
        /*
         * skill: the recovered 80x24 balloon frame (orange ally / blue enemy) with the canonical
         * icon, name and invocation label drawn INSIDE it. The native balloon is one opaque speech
         * bubble, so the label belongs on the banner - a second black pill underneath it is a
         * duplicate, not part of the recovered art.
         */
        const balloonFrame = item.side === "enemy" ? SKILL_BALLOON_FRAMES.enemy : SKILL_BALLOON_FRAMES.ally;
        const invocation = generatedReleaseInvocation(
          { invocationLevel: item.invocationLevel, casterUnitId: item.casterUnitId, skillId: item.skillId, slot: item.slot },
          units,
        );
        return (
          <div
            key={item.key}
            className="pointer-events-none absolute"
            style={{ left: item.anchor.left, top: `calc(${item.anchor.top} - 40px)`, transform: "translate(-50%, -100%) scale(0.3)", transformOrigin: "bottom center" }}
            data-skill-trigger={item.skillId}
          >
            {/*
              * The sheet is 80x48: two stacked 80x24 frames. `background-size` scales the art to the
              * banner box (the previous `transform: scale()` on a wrapper), so the label keeps its own
              * 9px type size instead of being scaled up with the balloon.
              */}
            <div
              data-skill-balloon-frame
              style={{
                position: "relative",
                display: "block",
                width: SKILL_BALLOON_SHEET.width * SKILL_BALLOON_SCALE,
                height: SKILL_BALLOON_SHEET.height * SKILL_BALLOON_SCALE,
                backgroundImage: `url(${SKILL_BALLOON_SHEET.file})`,
                backgroundSize: `${SKILL_BALLOON_SHEET.width * SKILL_BALLOON_SCALE}px ${
                  SKILL_BALLOON_SHEET.height * SKILL_BALLOON_FRAME_COUNT * SKILL_BALLOON_SCALE
                }px`,
                backgroundPosition: `0px -${balloonFrame * SKILL_BALLOON_SCALE}px`,
                imageRendering: "pixelated",
              }}
            >
              <div
                data-skill-balloon-label
                className="absolute inset-0 flex items-center justify-center gap-1 px-2.5 text-[9px] leading-none text-white"
                style={{ textShadow: "0 1px 1px rgba(0, 0, 0, 0.85)" }}
              >
                {item.icon ? (
                  <img src={item.icon} alt="" style={{ width: 12, height: 12, flexShrink: 0, imageRendering: "pixelated" }} />
                ) : null}
                <span className="min-w-0 text-center leading-tight">
                  <span className="block">{item.name ?? `skill ${item.skillId}`}</span>
                  {invocation === null ? null : <span className="block text-[7px] opacity-70">{invocationLabel(invocation)}</span>}
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Chest strip                                                         */
/* ------------------------------------------------------------------ */

/**
 * The fight on screen after a consumable click. `result` is the runner's own answer (kept verbatim
 * for the acceptance/prefix/stock report); `replay` is that answer's branch as it stands now, which
 * the branch-job poll extends until `complete`.
 */
type BranchView = {
  result: BattleInteractionResult;
  replay: BattleReplayResult;
  /** the clicked tick, held while the branch commits */
  commandTick: number;
  /** increments on every new click; the clock resets only on a new commit, never on an extension */
  commitId: number;
  /** the job that computes the rest of this branch, or null when the answer was already complete */
  jobId: string | null;
  complete: boolean;
  /** a plain-language note about the branch's state (pending window, failed job, ...) */
  note: string | null;
};

/** How often the client asks the branch job for the rest of the branch. */
const BRANCH_POLL_INTERVAL_MS = 400;


/** The real treasure sprite of one queued chest, or a labelled box when the row could not resolve. */
function ChestSprite({ icon, name }: { icon: string | null; name: string | null }) {
  if (!icon) {
    return (
      <span
        className="flex h-7 w-7 items-center justify-center rounded-sm border border-dashed border-muted-foreground/60 text-[8px] text-muted-foreground"
        data-chest-sprite="unresolved"
      >
        box
      </span>
    );
  }
  return (
    <img
      src={icon}
      alt={name ?? "treasure box"}
      className="h-7 w-7 object-contain [image-rendering:pixelated]"
      data-chest-sprite={name ?? "treasure"}
    />
  );
}

export function GeneratedBattleReplay({ initialRecord, embedded = false }: { initialRecord?: GeneratedBattleRecord; embedded?: boolean } = {}) {
  // Desktop traces stay in memory, avoiding Web Storage's small per-origin quota.
  const read = useMemo(() => initialRecord ? { status: "ok" as const, record: initialRecord } : readGeneratedBattle(), [initialRecord]);
  const [record] = useState<GeneratedBattleRecord | null>(read.status === "ok" ? read.record : null);
  /**
   * The fight on screen. Normally the stored run; after a consumable click it is the branch the
   * runner returned for that exact tick (INTERACTION-CONTRACT.md sections 2-7). The stored record is
   * never overwritten, so the player can go back to the original fight.
   *
   * `replay` is the branch as displayed right now: a clicked branch starts as the server's live
   * window (the true engine branch through the clicked tick plus a short playback window) and grows
   * through the branch-job poll until it is the whole fight. `complete` is false while that is still
   * pending, so a window is never presented as a finished battle.
   */
  const [branch, setBranch] = useState<BranchView | null>(null);
  const [localReplay, setLocalReplay] = useState<BattleReplayResult | null>(null);
  const localSession = useRef(browserBattleActive() && !initialRecord);
  const restoringRef = useRef(false);
  const advancingRef = useRef(false);
  const [busyConsumable, setBusyConsumable] = useState<string | null>(null);
  const busyConsumableRef = useRef<string | null>(null);
  /** the playback state the user had before the click; restored once the branch is committed */
  const resumePlayingRef = useRef(false);
  /** the tick the click landed on: playback is held there until the branch commits */
  const frozenTickRef = useRef<number | null>(null);
  /** the clock stopped at a WINDOW edge, so an extension must resume it */
  const windowEdgeStopRef = useRef(false);
  const commitCounterRef = useRef(0);
  const [interactionError, setInteractionError] = useState<string | null>(null);
  const displayedReplay: BattleReplayResult | null = branch?.replay ?? localReplay ?? record?.replay ?? null;

  useEffect(() => {
    if (initialRecord || localSession.current || restoringRef.current || !record?.replay.finalState.windowed || !record.scenarioJson) return;
    restoringRef.current = true;
    void startBrowserBattle(record.scenarioJson).then((payload) => {
      const parsed = parseBattleReplayResult(JSON.parse(payload));
      if (!parsed.result || parsed.issues.length) throw new Error(parsed.issues.join("; ") || "Invalid browser replay");
      localSession.current = true;
      setLocalReplay(parsed.result);
      setStep(0);
    }).catch((error) => {
      setInteractionError(`Could not resume the browser battle: ${error instanceof Error ? error.message : String(error)}`);
      setPlaying(false);
    }).finally(() => {
      restoringRef.current = false;
    });
  }, [initialRecord, record]);

  /**
   * The baked job/equipment rules, so a `/battle` fighter (job identity + equipment, no raw
   * `appearanceInputs`) resolves to the recovered human clip renderer instead of the static loadout
   * preview. Loaded once; until it arrives the stage keeps the previous fallback, so nothing flashes
   * as a placeholder.
   */
  const [humanRules, setHumanRules] = useState<GeneratedHumanRules | null>(null);
  useEffect(() => {
    let live = true;
    loadGeneratedHumanRules().then((loaded) => {
      if (live) setHumanRules(loaded);
    });
    return () => {
      live = false;
    };
  }, []);

  const units = useMemo(
    () => (record && displayedReplay ? buildGeneratedUnits(displayedReplay, record.visualSetup) : []),
    [record, displayedReplay],
  );
  /**
   * The frame clock is clamped to the runner's own ending cut (REGRESSION-CONTRACT.md section 1):
   * `finalState.ticks` is the declared tick limit, not the fight's end, so playing to it shows
   * thousands of post-verdict ticks with the whole team already gone. The cut keeps the real
   * knock-down / departure window and drops the silent tail; legacy payloads without a cut fall back
   * to the last event tick, which is the last frame anyway.
   */
  const playbackEndTick = useMemo(
    () => {
      if (!displayedReplay) return -1;
      const cut = battlePlaybackEndTick(displayedReplay);
      if (displayedReplay.finalState.windowed && typeof displayedReplay.finalState.verdictTick === "number") {
        return typeof displayedReplay.finalState.endingCutTick === "number"
          ? displayedReplay.finalState.endingCutTick
          : cut;
      }
      /*
       * A payload stored by the older packaged runtime carries no `endingCutTick`, so the helper falls
       * back to the last event tick - which can be the declared-Finish `chest_dispatch` parked at the
       * horizon (the reported "plays to 6999"). That event is a labelled diagnostic, not simulated
       * battle time (SHIP-RUNTIME-CONTRACT.md sections 2-3), so a legacy payload is clamped to its last
       * non-diagnostic event. Nothing is derived here; only which tick is played last.
       */
      if (displayedReplay.finalState.windowed && typeof displayedReplay.finalState.windowStopTick === "number") {
        return displayedReplay.finalState.windowStopTick;
      }
      if (typeof displayedReplay.finalState.endingCutTick === "number") return cut;
      let lastSimulated = -1;
      for (const event of displayedReplay.events as BattleReplayEvent[]) {
        if (event.kind === "chest_dispatch") continue;
        if (typeof event.tick === "number" && event.tick > lastSimulated) lastSimulated = event.tick;
      }
      return lastSimulated >= 0 ? Math.min(cut, lastSimulated) : cut;
    },
    [displayedReplay],
  );
  const frames = useMemo(() => {
    if (!displayedReplay) return [];
    return buildGeneratedFrames(displayedReplay).filter((entry) => entry.tick <= playbackEndTick);
  }, [displayedReplay, playbackEndTick]);
  const coverage = useMemo(
    () => (displayedReplay ? generatedCoverage(displayedReplay, units, record?.warnings ?? []) : null),
    [displayedReplay, units, record],
  );
  const skillEvents = useMemo(() => (displayedReplay ? generatedSkillEvents(displayedReplay) : []), [displayedReplay]);
  const skillReleases = useMemo(() => (displayedReplay ? generatedSkillReleases(displayedReplay) : []), [displayedReplay]);
  const outcome = useMemo(() => (displayedReplay ? generatedFinishOutcome(displayedReplay) : null), [displayedReplay]);
  const rewardSummary = useMemo(() => (outcome ? generatedRewardSummary(outcome) : null), [outcome]);
  const chestDrops = useMemo(() => (displayedReplay ? generatedChestDrops(displayedReplay) : []), [displayedReplay]);
  const chestSummary = useMemo(() => (outcome ? generatedChestSummary(outcome, chestDrops) : null), [outcome, chestDrops]);
  const totals = useMemo(() => {
    const damage = new Map<string, number>();
    const healing = new Map<string, number>();
    if (!displayedReplay) return { damage, healing };
    for (const event of displayedReplay.events as BattleReplayEvent[]) {
      const value =
        typeof event.damage === "number"
          ? event.damage
          : typeof event.amount === "number"
            ? event.amount
            : null;
      if (value === null || !event.targetUnitId) continue;
      const group = generatedEventGroup(event.kind);
      if (group === "damage") damage.set(event.targetUnitId, (damage.get(event.targetUnitId) ?? 0) + value);
      if (group === "heal") healing.set(event.targetUnitId, (healing.get(event.targetUnitId) ?? 0) + value);
    }
    return { damage, healing };
  }, [displayedReplay]);
  const scenarioSource = useMemo(() => interactionScenarioFromRecord(record), [record]);
  const activeScenario = branch?.result.scenario ?? (scenarioSource.ok ? scenarioSource.scenario : undefined);
  const downloadFight = () => {
    if (!activeScenario || !displayedReplay || !record) return;
    try {
      if (activeScenario.schema !== "ka-special-combat-research-1" || !Array.isArray(activeScenario.inputs)) {
        throw new Error("This fight has no valid scenario to export.");
      }
      const archive = optimizerFightExport(activeScenario as CombatScenario, displayedReplay, record);
      downloadJson(optimizerScenarioFilename(record.summary.encounterTitle ?? `Encounter ${record.summary.encounterId}`).replace(/\.scenario\.json$/, ".fight.json"), archive);
      setInteractionError(null);
    } catch (error) {
      setInteractionError(error instanceof Error ? error.message : "Could not download this fight.");
    }
  };
  const downloadFightScenario = () => {
    if (!activeScenario || !displayedReplay || !record) return;
    try {
      if (activeScenario.schema !== "ka-special-combat-research-1" || !Array.isArray(activeScenario.inputs)) {
        throw new Error("This fight has no valid scenario to export.");
      }
      const archive = optimizerFightExport(activeScenario as CombatScenario, displayedReplay, record);
      downloadJson(optimizerScenarioFilename(record.summary.encounterTitle ?? `Encounter ${record.summary.encounterId}`), archive.scenario);
      setInteractionError(null);
    } catch (error) {
      setInteractionError(error instanceof Error ? error.message : "Could not download this fight's scenario.");
    }
  };
  const transport = useMemo(() => createHttpBattleInteractionTransport(), []);
  const branchPollTransport = useMemo(() => createHttpBattleInteractionPollTransport(), []);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  /** playback speed multiplier; one step is always one authoritative replay tick (50 ms native) */
  const [speed, setSpeed] = useState(1);
  /** the end-of-fight popup is dismissible; moving the clock brings it back */
  const [verdictDismissed, setVerdictDismissed] = useState(false);
  /**
   * The shared stage needs the reference presentation scale: one transform for scenery and
   * fighters, measured from the host width. Nothing here decides combat state.
   */
  const stageHostRef = useRef<HTMLDivElement>(null);
  const [sceneScale, setSceneScale] = useState(1);
  const viewProfile = viewProfileById(DEFAULT_VIEW_PROFILE_ID);
  const backgroundX = backgroundXForViewWidth(viewProfile.logicalViewWidth);
  const presentationRatio = verticalPresentationRatio(viewProfile);
  const lastStep = Math.max(0, frames.length - 1);
  useEffect(() => {
    const host = stageHostRef.current;
    if (!host) return;
    const update = () => {
      const width = host.clientWidth;
      if (width > 0) setSceneScale(width / viewProfile.logicalViewWidth);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(host);
    return () => observer.disconnect();
  }, [viewProfile.logicalViewWidth]);

  useEffect(() => {
    if (!playing || frames.length === 0) return;
    // The recovered battle cadence is 20 ticks per second (50 ms); one timer tick = one replay tick.
    const timer = window.setInterval(() => {
      setStep((current) => busyConsumableRef.current ? current : Math.min(current + 1, lastStep));
    }, Math.max(1, Math.round(50 / speed)));
    return () => window.clearInterval(timer);
  }, [playing, frames.length, speed, lastStep]);

  useEffect(() => {
    if (!localSession.current || !displayedReplay?.finalState.windowed || advancingRef.current || busyConsumableRef.current) return;
    const edge = displayedReplay.finalState.windowStopTick;
    if (typeof edge !== "number") return;
    const shownTick = frames[Math.min(step, lastStep)]?.tick ?? 0;
    const verdictTick = displayedReplay.finalState.verdictTick;
    if (typeof verdictTick === "number") {
      if (edge >= verdictTick + 120) return;
    } else if (edge - shownTick > 60) return;
    advancingRef.current = true;
    void advanceBrowserBattle(100).then((payload) => {
      const parsed = parseBattleReplayResult(JSON.parse(payload));
      if (!parsed.result || parsed.issues.length) throw new Error(parsed.issues.join("; ") || "Invalid browser replay");
      setLocalReplay(parsed.result);
      if (windowEdgeStopRef.current && (
        typeof parsed.result.finalState.verdictTick !== "number" ||
        (parsed.result.finalState.endingCutTick ?? -1) > shownTick
      )) {
        windowEdgeStopRef.current = false;
        setPlaying(true);
      }
    }).catch((error) => {
      setPlaying(false);
      setInteractionError(`Browser simulation stopped: ${error instanceof Error ? error.message : String(error)}`);
    }).finally(() => {
      advancingRef.current = false;
    });
  }, [displayedReplay, frames, step, lastStep]);

  // The clock stops on the final frame instead of silently restarting at frame 0.
  useEffect(() => {
    if (playing && frames.length > 0 && step >= lastStep) {
      setPlaying(false);
      /*
       * Reaching the end of a WINDOW is not the end of the fight: remember that the clock stopped
       * because the branch is still resolving, so the next extension resumes playback by itself.
       */
      windowEdgeStopRef.current = Boolean((branch && !branch.complete) || (localSession.current && displayedReplay?.finalState.windowed));
    }
  }, [playing, step, frames.length, lastStep, branch, displayedReplay]);

  /**
   * After a branch COMMITS, keep watching the same tick instead of jumping: the branch preserves
   * every event before the click tick, so that tick sits at the same place in the new timeline, and
   * playback resumes exactly as the user left it (paused stays paused, playing keeps playing). An
   * extension of the same branch never resets the clock, so `commitId` is the dependency.
   */
  useEffect(() => {
    if (!branch) return;
    setVerdictDismissed(false);
    windowEdgeStopRef.current = false;
    const index = generatedFrameIndexForTick(frames, branch.commandTick);
    if (index >= 0) setStep(index);
    setPlaying(resumePlayingRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branch?.commitId]);

  const useConsumable = (row: GeneratedConsumableRow) => {
    /*
     * Click-time capture and the in-flight guard are synchronous: the tick comes from the frame the
     * user was watching, the clock freezes before any request exists, and a second click while a
     * request is in flight is answered with a reason instead of being silently dropped.
     */
    const candidateFrame = frames[Math.min(Math.max(step, 0), Math.max(frames.length - 1, 0))];
    const verdictTick = displayedReplay?.finalState.verdictTick;
    const plan = planConsumableClick({
      frames,
      step,
      playing,
      busy: busyConsumableRef.current,
      ended:
        candidateFrame !== undefined &&
        typeof verdictTick === "number" &&
        candidateFrame.tick >= verdictTick,
      sourceIssue: activeScenario ? null : scenarioSource.ok ? "no source scenario" : scenarioSource.reason,
    });
    if (!plan.ok) {
      setInteractionError(plan.reason);
      return;
    }
    void sendConsumable(row, plan.tick, plan.resumePlaying);
  };

  const sendConsumable = async (row: GeneratedConsumableRow, tick: number, resumePlaying: boolean) => {
    if (!displayedReplay) return;
    if (localSession.current) {
      setPlaying(false);
      busyConsumableRef.current = row.key;
      setBusyConsumable(row.key);
      setInteractionError(null);
      try {
        const payload = await useBrowserBattleItem(row.key, tick);
        const parsed = parseBattleReplayResult(JSON.parse(payload));
        if (!parsed.result || parsed.issues.length) {
          throw new Error(parsed.issues.join("; ") || "Invalid browser replay");
        }
        setLocalReplay(parsed.result);
        setStep(generatedFrameIndexForTick(buildGeneratedFrames(parsed.result), tick));
        setPlaying(resumePlaying);
      } catch (error) {
        setInteractionError(`Item use failed: ${error instanceof Error ? error.message : String(error)}`);
        setPlaying(resumePlaying);
      } finally {
        busyConsumableRef.current = null;
        setBusyConsumable(null);
      }
      return;
    }
    if (!activeScenario) {
      setInteractionError(scenarioSource.ok ? "no source scenario" : scenarioSource.reason);
      return;
    }
    frozenTickRef.current = tick;
    resumePlayingRef.current = resumePlaying;
    setPlaying(false);
    busyConsumableRef.current = row.key;
    setBusyConsumable(row.key);
    setInteractionError(null);
    const built = buildBattleInteractionRequest({
      scenario: activeScenario,
      item: row.key,
      tick,
      displayedReplay,
      windowTicks: BATTLE_INTERACTION_WINDOW_TICKS,
    });
    if (!built.ok) {
      setInteractionError(built.issues.map((issue) => issue.message).join("; "));
      busyConsumableRef.current = null;
      setBusyConsumable(null);
      return;
    }
    const outcomeResult = await requestBattleInteraction(built.body, transport);
    busyConsumableRef.current = null;
    setBusyConsumable(null);
    if (!outcomeResult.ok) {
      setInteractionError(`${outcomeResult.code}: ${outcomeResult.message}`);
      return;
    }
    const result = outcomeResult.result;
    /*
     * Trust check (INTERACTION-CONTRACT.md section 5): the branch must preserve every event before the
     * clicked tick. If it does not, the two fights are not the same fight and the branch is refused.
     */
    const preserved = preservedPrefixLength(displayedReplay, result.replay, result.command.tick);
    if (preserved < result.prefix.eventCount) {
      setInteractionError(
        `the branch changed ${result.prefix.eventCount - preserved} event(s) before tick ${result.command.tick}, so it was not shown`,
      );
      return;
    }
    commitCounterRef.current += 1;
    const windowed = result.window && !result.window.complete;
    setBranch({
      result,
      replay: result.replay,
      commandTick: result.command.tick,
      commitId: commitCounterRef.current,
      jobId: result.window?.jobId ?? null,
      complete: !windowed,
      note: windowed
        ? "Preparing the rest of the battle…"
        : null,
    });
  };

  /*
   * The rest of a windowed branch. One poll at a time, each returning only the events after what is
   * displayed; a splice is only applied when the reply's own prefix count matches this replay at the
   * same boundary (mergeBranchExtension), so the displayed fight is always one continuous branch.
   * A window is never reported as the end of the fight: only `complete` clears the pending note.
   */
  useEffect(() => {
    if (!branch || !branch.jobId || branch.complete) return;
    const jobId = branch.jobId;
    const displayed = branch.replay;
    let cancelled = false;
    let timer: number | undefined;
    const schedule = () => {
      if (!cancelled) timer = window.setTimeout(poll, BRANCH_POLL_INTERVAL_MS);
    };
    const poll = async () => {
      if (cancelled) return;
      if (busyConsumableRef.current) { schedule(); return; }
      const fromTick = interactionFromTick(displayed);
      const reply = await requestBattleBranchPoll(
        { schema: BATTLE_INTERACTION_POLL_SCHEMA, jobId, fromTick },
        branchPollTransport,
      );
      if (cancelled) return;
      if (busyConsumableRef.current) { schedule(); return; }
      if (reply.ok) {
        if (reply.reply.jobId !== jobId) {
          setInteractionError("The update belongs to a different battle branch and was not applied.");
          return;
        }
        const merged = mergeBranchExtension(displayed, {
          replay: reply.reply.replay,
          prefixEventCount: reply.reply.prefixEventCount,
          fromTick,
        });
        if (!merged.ok) {
          setInteractionError(`the branch extension was refused: ${merged.reason}`);
          return;
        }
        const complete = reply.reply.window.complete || reply.reply.state === "ready";
        setBranch((current) =>
          current && current.jobId === jobId
            ? {
                ...current,
                replay: merged.replay,
                complete,
                note: complete
                  ? null
                  : "Preparing the rest of the battle…",
              }
            : current,
        );
        if (windowEdgeStopRef.current) {
          windowEdgeStopRef.current = false;
          setPlaying(true);
        }
        if (!complete) schedule();
        return;
      }
      if (reply.code === "interaction-job-pending") {
        schedule();
        return;
      }
      if (reply.code === "interaction-job-unknown") {
        setInteractionError(
          "the rest of this branch is no longer available (a newer click replaced it), so only the " +
            "window the runner already sent is shown",
        );
        setBranch((current) => (current && current.jobId === jobId ? { ...current, jobId: null } : current));
        return;
      }
      setInteractionError(`${reply.code}: ${reply.message}`);
      setBranch((current) =>
        current && current.jobId === jobId
          ? { ...current, jobId: null, note: "the rest of this branch could not be computed; only the window is shown" }
          : current,
      );
    };
    schedule();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [branch, branchPollTransport]);

  if (read.status === "empty") {
    return (
      <div className="mx-auto max-w-4xl space-y-4 px-4 py-6" data-generated-replay="empty">
        <Card>
          <CardHeader>
            <CardTitle>No generated battle</CardTitle>
            <CardDescription>
              No battle has been run in this session, so nothing generated can be shown. The recovered
              reference replay stays available below.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button asChild size="sm">
              <Link href="/battle-setup" data-action="back-to-setup">
                Back to Setup
              </Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link href="/battle-replay" data-action="use-reference-replay">
                Use reference replay
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }
  if (read.status === "incompatible" || !record || !displayedReplay || !coverage || !outcome || !chestSummary) {
    return (
      <div className="mx-auto max-w-4xl space-y-4 px-4 py-6" data-generated-replay="incompatible">
        <Card>
          <CardHeader>
            <CardTitle>Stored battle is not compatible</CardTitle>
            <CardDescription>{read.status === "incompatible" ? read.reason : "no record"}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button asChild size="sm">
              <Link href="/battle-setup" data-action="back-to-setup">
                Back to Setup
              </Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link href="/battle-replay">Use reference replay</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const frame = frames[Math.min(step, lastStep)];
  const currentTick = frame?.tick ?? 0;
  const allies = units.filter((unit) => unit.side === "ally");
  const enemies = units.filter((unit) => unit.side === "enemy");
  /**
   * The readable ally HP/MP block. It reads the replay frame exactly as the stage gauges do, so a dark
   * or crowded battlefield never hides an ally's numbers. Nothing is derived and nothing pre-applied.
   */
  const allyVitals: AllyVitalRow[] = allies.map((unit) => {
    const live = frame?.units[unit.unitId];
    return {
      unitId: unit.unitId,
      name: unit.name,
      hp: live?.hp ?? unit.startHp,
      maxHp: live?.maxHp ?? unit.maxHp,
      mp: live?.mp ?? unit.startMp,
      maxMp: live?.maxMp ?? unit.maxMp,
      dead: live?.dead ?? false,
    };
  });
  const nameOfUnit = (unitId?: string | null) =>
    units.find((unit) => unit.unitId === unitId)?.name ?? unitId ?? "-";
  const sideOfUnit = (unitId?: string | null): "ally" | "enemy" | null =>
    units.find((unit) => unit.unitId === unitId)?.side ?? null;
  /*
   * Totals are tallies of the runner's own event values and final state. This view never
   * re-simulates and never derives a verdict of its own.
   */
  /* the generated arena is not recovered yet (coverage: ARENA PARTIAL), so the shared backdrop is used */
  const arena = ARENAS.find((entry) => entry.id === SHARED_ARENA_ID) ?? ARENAS[0];
  /*
   * Gauges read the replay frame's real HP/MP against the runner's true maxima (param 10/11) and are
   * gated by the CONFIRMED native `IsMostFront` rule inside `generatedFighterVitals`, so the bar
   * block appears on exactly the front row of each team - and appears there whatever art the fighter
   * resolved to.
   */
  const vitalsFor = (unit: GeneratedUnitView): FighterVitals | null =>
    generatedFighterVitals(unit.side, frame?.units[unit.unitId], rowOffset);
  /*
   * What the stage really draws, per fighter, taken from the same branches the stage JSX takes. A
   * human whose raw appearance inputs are absent but whose job identity is known is drawn by the
   * shared loadout renderer - it is NOT a placeholder, so it must not be counted as one.
   */
  const renderTally = generatedRenderTally(
    units,
    (monsterId) => monsterSpriteById(monsterId) !== null,
    humanRules,
  );
  const placeholderCount = renderTally.placeholderCount;
  const missingMonsterArtCount = renderTally.placeholders.filter((entry) => entry.missing === "monster-art").length;
  const missingAppearanceCount = renderTally.placeholders.filter((entry) => entry.missing === "human-appearance").length;
  /*
   * The visual builder (`/battle`) is the only writer that stores the source scenario with the run
   * (`GeneratedBattleRecord.scenarioJson`), so a record carrying one is the fight the player just
   * built there: its "back" link returns to that builder, and the raw setup editor stays reachable as
   * an explicit diagnostics link. A fight from the raw setup editor keeps its own link.
   */
  const fromVisualBuilder = typeof record.scenarioJson === "string" && record.scenarioJson.length > 0;
  const setupHref = fromVisualBuilder ? "/battle" : "/battle-setup";
  /*
   * The stage note is written from the resolved render paths, never from absent appearance inputs
   * alone: a fighter the shared loadout renderer draws is drawn, and only a fighter with neither its
   * own art nor a job identity is reported as a placeholder.
   */
  const stageFallbackNote = (() => {
    const previews = renderTally.loadoutPreview;
    const placeholders = renderTally.placeholderCount;
    const plural = (count: number) => (count === 1 ? "" : "s");
    const artClause =
      previews === 0
        ? "Every fighter is drawn from its own recovered art"
        : `${previews} fighter${plural(previews)} drawn by the shared loadout renderer, the rest from their own recovered art`;
    if (placeholders === 0) return `${artClause}.`;
    return (
      `${artClause}; ${placeholders} fighter${plural(placeholders)} ` +
      `${placeholders === 1 ? "keeps" : "keep"} a labelled placeholder - this replay carries no battle art or job identity for them.`
    );
  })();
  /*
   * One camera for the whole scene. Fighters are placed from the recovered native projection
   * (cell -> isometric -> minus the battle camera, the same rule the reference replay draws), and the
   * presentation window slides over the 481x197 scene by `viewPanX` so the formation sits inside the
   * recorded-width window. The pan comes from the units' own initial cells, so a fighter launched off
   * the field cannot drag the view away from the live fight.
   *
   * Depth ordering uses the recovered stationary queue rule
   * `depth = 24 * (cell.column + cell.row) + lineIndex` with lineIndex 0 for a whole generated unit,
   * painted in ascending order; ties keep insertion order, exactly like the shared queue.
   */
  const rowOffset = nativeInitialFormation(allies.length, enemies.length).rowOffset;
  const viewPanX = generatedViewPanX(
    units.map((unit) => [unit.initialCell[0], unit.initialCell[1]] as [number, number]),
    rowOffset,
    viewProfile.logicalViewWidth,
  );
  const stageEntries = units
    .map((unit) => {
      const unitFrame = frame?.units[unit.unitId];
      const cell = unitFrame?.cell ?? unit.initialCell;
      return {
        unit,
        unitFrame,
        cell,
        depth: 24 * (cell[0] + cell[1]),
        placement: generatedScenePlacement({
          cell,
          unitFrame,
          tick: currentTick,
          rowOffset,
          viewPanX,
        }),
      };
    })
    .sort((a, b) => a.depth - b.depth);
  const anchors = new Map(stageEntries.map((entry) => [entry.unit.unitId, entry.placement]));
  /*
   * The impact effect is a ten-frame animation (`effect_00.seb` maxFrame 10), so a landed hit keeps
   * being drawn while its burst plays instead of only on the tick it landed. The window is the
   * displayed frame plus the ticks before it, so the burst grows through its recovered cells and then
   * stops. Skill labels use a separate readability window; other overlays stay on their event tick.
   */
  const impactEndStep = Math.min(step, lastStep);
  const impactWindow = frames.slice(
    Math.max(0, impactEndStep - (GENERATED_IMPACT_LIFETIME_TICKS - 1)),
    impactEndStep + 1,
  );
  // Presentation timing only: retain successful skill labels for two seconds of playback.
  // Keep the latest release per caster so repeated casts do not pile up on the same sprite.
  const skillLabels = new Map<string, BattleReplayEvent>();
  for (const recentFrame of frames.slice(Math.max(0, impactEndStep - Math.ceil(40 * speed) + 1), impactEndStep + 1)) {
    for (const event of recentFrame.events) {
      if (event.kind.toLowerCase() === "release" && event.used === true) {
        skillLabels.set(event.casterUnitId ?? event.actorUnitId ?? String(event.seq), event);
      }
    }
  }
  const overlays = buildGeneratedStageOverlays({
    events: [...(frame?.events ?? []).filter((event) => event.kind.toLowerCase() !== "release"), ...skillLabels.values()],
    anchorOf: (unitId) => (unitId ? anchors.get(unitId) ?? null : null),
    sideOf: sideOfUnit,
    impactEvents: impactWindow.flatMap((impactFrame) => impactFrame.events),
    impactTick: currentTick,
  });
  const hpTotals = (roster: GeneratedUnitView[]) =>
    roster.reduce(
      (sum, unit) => {
        const live = frame?.units[unit.unitId];
        return {
          current: sum.current + (live?.hp ?? unit.startHp),
          maximum: sum.maximum === null || (live?.maxHp ?? unit.maxHp) === null
            ? null : sum.maximum + ((live?.maxHp ?? unit.maxHp) as number),
        };
      },
      { current: 0, maximum: 0 as number | null },
    );
  const aliveCount = (roster: GeneratedUnitView[]) =>
    roster.filter((unit) => !(frame?.units[unit.unitId]?.dead ?? false)).length;
  const allyHp = hpTotals(allies);
  const enemyHp = hpTotals(enemies);
  const tickSummary = generatedTickSummary(frame?.events ?? [], nameOfUnit);
  const outcomeWord = generatedVerdictWord(outcome.verdict);
  const atFinalTick = step >= lastStep;
  /*
   * The verdict tick and the playback cut are two different ticks (SHIP-RUNTIME-CONTRACT.md section 2):
   * Ending is entered at `verdictTick` (a whole team in Leaving state 8) and the engine keeps the
   * knock-down / departure window up to `endingCutTick`. The popup and the badge bind to the verdict
   * tick; playback binds to the cut. `endedCutTick` is diagnostics only.
   */
  const verdictTick = displayedReplay.finalState.verdictTick ?? displayedReplay.finalState.endingTick ?? null;
  /* a rebuilt payload carries the verdict tick; a legacy one falls back to the cut so the popup still fires */
  const reachedVerdict =
    outcome.verdict !== null && (verdictTick === null ? atFinalTick : currentTick >= verdictTick);
  const awardedBasis = outcome.rewardEntitlement?.awardedChestCountBasis ?? null;
  const liveWord = reachedVerdict ? outcomeWord : "Fighting...";
  const chestsSoFar = generatedChestsAt(chestDrops, currentTick);
  /** only the chests the runner has already queued by the displayed tick - never a later drop */
  const visibleChestDrops = chestDrops.filter((drop) => drop.tick <= currentTick);
  /** only the skills already released by the displayed tick - a later release is not "already used" */
  const visibleSkillReleases = skillReleases.filter((release) => release.tick <= currentTick);
  /** the raw skill log is filtered to the displayed tick too, so no future event is ever shown */
  const visibleSkillEvents = skillEvents.filter((event) => event.tick <= currentTick);
  const consumableRows = generatedConsumableRows({
    replay: displayedReplay,
    tick: currentTick,
    scenario: activeScenario,
    frameUnits: frame?.units ?? null,
  });
  /* the same sentence reaches the item buttons and the diagnostics card: no internal vocabulary */
  const consumableBlockedReason = scenarioSource.ok ? null : `Items are unavailable here: ${scenarioSource.reason}.`;
  const currentSkill = overlays.find((item) => item.kind === "skill");
  /*
   * A notice that never renders as a bordered empty box: the runner's acceptance is free to omit
   * `reason`, so a blank one falls back to what the acceptance itself reports, and the verb follows
   * the acceptance too (a refused use is "Tried", not "Used").
   */
  const branchReason = branch
    ? branch.result.acceptance.reason.trim() ||
      (branch.result.acceptance.used ? "the fight continued with it" : "no fighter changed")
    : null;
  const branchNotice = branch
    ? `${branch.result.acceptance.used ? "Used" : "Tried"} ${
        branch.result.command.item === "holy_herb" ? "Holy Herb" : branch.result.command.item
      } at tick ${branch.result.command.tick}: ${branchReason}`
    : null;

  return (
    <div className="mx-auto max-w-6xl space-y-3 px-3 py-4 sm:px-4 sm:py-6" data-generated-replay="loaded"
      data-generated-store-version={GENERATED_BATTLE_STORE_VERSION}
      data-generated-encounter={record.summary.encounterId}
      data-generated-frame={frame?.index ?? 0}
      data-generated-tick={currentTick}
      data-generated-frame-count={frames.length}
      data-generated-max-tick={frames.length ? frames[frames.length - 1].tick : 0}
      data-generated-min-tick={frames.length ? frames[0].tick : 0}
      data-generated-interval-ms={Math.max(1, Math.round(50 / speed))}
      data-generated-playing={playing ? "1" : "0"}
      data-generated-branched={branch ? "1" : "0"}
      data-generated-chests={chestsSoFar}
      data-generated-origin={fromVisualBuilder ? "visual-builder" : "setup"}>
      {/* Top bar: identity, verdict badge, back links. The controls live inside the arena frame. */}
      <div className="flex flex-wrap items-center justify-between gap-2" data-generated-topbar>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="truncate text-base font-semibold">
            {record.summary.encounterTitle ?? `Encounter ${record.summary.encounterId}`}
          </h2>
          <Badge variant={reachedVerdict && outcome.verdict === 1 ? "default" : "secondary"} data-generated-outcome>
            {liveWord}
          </Badge>
          {branch ? <Badge variant="outline" data-generated-branch>variant fight</Badge> : null}
          <span className="text-xs text-muted-foreground" data-generated-subtitle>
            {allies.length} allies vs {enemies.length} enemies · tick {currentTick} of{" "}
            {frames.length ? frames[frames.length - 1].tick : 0}
          </span>
        </div>
        {!embedded ? <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" className="min-h-11" onClick={downloadFightScenario} disabled={!activeScenario || displayedReplay.finalState.windowed || Boolean(branch && !branch.complete)} title="Import this scenario with the desktop optimizer; it includes the consumables used">
            Optimizer JSON
          </Button>
          <Button size="sm" variant="outline" className="min-h-11" onClick={downloadFight} disabled={!activeScenario || displayedReplay.finalState.windowed || Boolean(branch && !branch.complete)} title="Download the complete replay, setup, and consumable actions">
            Download fight JSON
          </Button>
          {/*
           * The primary "back" returns to the surface this fight was built on; a fight built in
           * the visual builder goes back there, and the raw setup editor is a separate diagnostic.
           */}
          <Button asChild size="sm" data-action="back-to-setup">
            <Link href={setupHref} data-generated-back={fromVisualBuilder ? "visual-builder" : "setup"}>
              {fromVisualBuilder ? "Back to Battle" : "Back to Setup"}
            </Link>
          </Button>
          {fromVisualBuilder ? (
            <Button asChild size="sm" variant="outline" data-action="back-to-raw-setup">
              <Link href="/battle-setup">Raw setup (diagnostics)</Link>
            </Button>
          ) : null}
          <Button asChild size="sm" variant="outline">
            <Link href="/battle-replay">Reference replay</Link>
          </Button>
        </div> : null}
      </div>

      {branchNotice ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/5 px-3 py-2 text-xs" data-generated-branch-notice>
          <span>{branchNotice}</span>
          <Button size="sm" variant="outline" onClick={() => { setBranch(null); setPlaying(false); setStep(0); }} data-action="show-original">
            Show the original fight
          </Button>
        </div>
      ) : null}
      {interactionError ? (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs" data-generated-interaction-error>
          {interactionError}
        </p>
      ) : null}

      {/*
       * The arena frame: the battle stage, a compact HUD along its top edge (treasure strip, team
       * status, readable ally HP/MP, the current tick) and the action bar with the playback controls
       * and the consumable buttons inside the same frame, so battlefield and items share one screen.
       * `src/index.css` caps the arena from the viewport height, so a short desktop window shrinks the
       * battlefield rather than separating the HUD from the items.
       */}
      <div className="ka-frame" data-battle-frame>
        <div className="ka-frame__hud">
          <BattleTreasureStrip
            queued={chestsSoFar}
            drops={visibleChestDrops}
            atEnd={atFinalTick && !displayedReplay.finalState.windowed}
            awarded={chestSummary.awarded}
            awardedBasis={awardedBasis}
            pending={chestSummary.pending}
          />
          <div className="ka-hud-status" data-generated-live-summary>
            <span>
              Allies {aliveCount(allies)}/{allies.length} · Enemies {aliveCount(enemies)}/{enemies.length}
            </span>
            <span className="text-muted-foreground">
              Ally HP {allyHp.current}/{allyHp.maximum ?? "?"} · Enemy HP {enemyHp.current}/{enemyHp.maximum ?? "?"}
            </span>
          </div>
          <BattleAllyVitals rows={allyVitals} />
          <div className="ka-hud-tick" data-generated-tick-summary>
            <span className="ka-hud-tick__label">tick {currentTick}</span>
            <span className="ka-hud-tick__text" title={tickSummary.lines.length ? tickSummary.lines.join(" · ") : "No visible change at this tick."}>
              {tickSummary.lines.length ? tickSummary.lines.join(" · ") : "No visible change at this tick."}
            </span>
            {currentSkill && currentSkill.kind === "skill" ? (
              <span className="ka-hud-chip" data-generated-skill-trigger={currentSkill.skillId}>
                {currentSkill.name ?? currentSkill.skillId} released
              </span>
            ) : null}
          </div>
        </div>
        <div className="ka-frame__stage">

          <BattleStage
            hostRef={stageHostRef}
            viewProfileId={viewProfile.id}
            logicalViewWidth={viewProfile.logicalViewWidth}
            sourceHeight={viewProfile.sourceHeight}
            sceneScale={sceneScale}
            presentationScale={1}
            presentationRatio={presentationRatio}
            backgroundX={backgroundX - viewPanX}
            arenaFile={arena.file}
            rowOffset={rowOffset}
          >
            {stageEntries.map(({ placement, unit, unitFrame, cell, depth }) => {
              const side = unit.side;
              const mode = generatedStageMode(unit, humanRules);
              const bareMonster = mode === "monster" && !monsterSpriteById(unit.monsterId);
              const character = mode === "human-clip" ? generatedHumanCharacter(unit, humanRules) : null;
              const direction = BATTLE_DIRECTIONS[side];
              const lines = character && unitFrame
                ? generatedHumanLinesForFrame(
                    unitFrame,
                    side === "ally" ? 0 : 2,
                    generatedFrameClipId(unit, unitFrame),
                  )
                : null;
              const monsterClip =
                mode === "monster" && unitFrame
                  ? generatedMonsterClip(unitFrame, currentTick, direction)
                  : null;
              /* one gate for both render branches: the CONFIRMED native IsMostFront hp/mp block */
              const gauge = vitalsFor(unit);
              if (mode === "generic-avatar" || bareMonster) {
                const reason =
                  mode === "generic-avatar"
                    ? "battle appearance inputs not in this replay"
                    : "battle art not recovered yet";
                const preview = mode === "generic-avatar" ? generatedEquippedPreview(unit) : null;
                return (
                  <div
                    key={`${side}-${unit.unitId}`}
                    className="absolute"
                    data-unit-root
                    data-side={side}
                    data-unit-index={unit.rosterIndex}
                    data-generic-avatar={reason}
                    data-generated-unit-id={unit.unitId}
                    data-generated-depth={depth}
                    data-generated-cell={cell.join(",")}
                    data-generated-visual={unitFrame?.visual ?? "waiting"}
                    data-generated-status={unitFrame?.status.join("|") ?? ""}
                    data-generated-sprite={preview ? "loadout-preview" : "placeholder"}
                    style={{ left: placement.left, top: placement.top, transform: "translate(-50%, -50%)" }}
                  >
                    {/*
                      * Art availability must never hide the native bars: the CONFIRMED DrawGauges
                      * gate is IsMostFront alone, with no art, human/monster or boss branch, so a
                      * front-row fighter draws its hp/mp block here too.
                      */}
                    {gauge ? (
                      <div className="pointer-events-none absolute left-0 top-0">
                        <NativeFighterGauges vitals={gauge} />
                      </div>
                    ) : null}
                    {preview ? (
                      <LoadoutStageSprite
                        params={{ ...preview, scale: 2, poseFrame: 0 }}
                        anchor={{ left: "0px", top: "0px" }}
                      />
                    ) : (
                      <span className="flex h-10 w-10 items-center justify-center rounded-sm border border-dashed border-muted-foreground/60 bg-black/40 text-center text-[7px] leading-tight text-muted-foreground">
                        placeholder
                      </span>
                    )}
                  </div>
                );
              }
              return (
                <UnitSprite
                  key={`${side}-${unit.unitId}`}
                  placement={{
                    unit: {
                      monsterId: unit.monsterId,
                      label: unit.name,
                      jobName: unit.jobId,
                      rank: unit.rank === undefined ? undefined : String(unit.rank),
                      isBoss: false,
                    } as unknown as BattleUnit,
                    index: unit.rosterIndex,
                    slot: { column: cell[0], row: cell[1] },
                    cell: { column: cell[0], row: cell[1] },
                    dead: (unitFrame?.dead ?? false) && !(unitFrame?.leaving ?? false),
                    left: placement.left,
                    top: placement.top,
                  }}
                  side={side}
                  showLabel={false}
                  debugFormation={false}
                  nativeBody
                  anchorBottom
                  nativeSize={{ width: 40, height: 30 }}
                  sebClip={monsterClip?.clip ?? null}
                  sebFrame={monsterClip?.update ?? 0}
                  sebMirror={MONSTER_DIRECTION_VARIANTS[direction].mirror}
                  humanCharacter={character}
                  humanLines={lines}
                  fighterGauge={gauge}
                  dataAttributes={{
                    "generated-unit-id": unit.unitId,
                    "generated-depth": depth,
                    "generated-cell": cell.join(","),
                    "generated-visual": unitFrame?.visual ?? "waiting",
                    "generated-status": unitFrame?.status.join("|") ?? "",
                  }}
                />
              );
            })}
            <StageOverlayLayer items={overlays} units={units} />
          </BattleStage>
          {/*
           * The verdict popup binds to the runner's own verdict tick (`finalState.verdictTick`), the frame
           * Ending was entered, not to the playback cut and not to the declared horizon. Playback still runs
           * on to `battlePlaybackEndTick` so the knock-down / departure window is kept.
           */}
          {reachedVerdict && !verdictDismissed ? (
            <>
              <BattleVerdictPopup
                word={outcomeWord}
                verdict={outcome.verdict}
                tick={verdictTick}
                censored={outcome.censored}
                liveWindow={Boolean(displayedReplay.finalState.windowed)}
                queued={chestSummary.queued}
                awarded={chestSummary.awarded}
                awardedBasis={awardedBasis}
                onRestart={() => {
                  setStep(0);
                  setPlaying(true);
                  setVerdictDismissed(false);
                }}
              />
              <Button
                size="sm"
                variant="outline"
                className="ka-verdict__close"
                onClick={() => setVerdictDismissed(true)}
                data-action="dismiss-verdict"
              >
                Close
              </Button>
            </>
          ) : null}
        </div>
        <div className="ka-frame__actions" data-battle-action-bar>
          {/*
           * Items first: the shelf sits directly under the battlefield it acts on, and the playback
           * transport closes the frame underneath it.
           */}
          {!embedded ? <>
          <div className="ka-hud-actionlabel">
            <span className="ka-hud-strip__label">Items</span>
            <span className="ka-hud-actionlabel__hint">tap to use one now</span>
          </div>
          <ConsumableActionBar
            rows={consumableRows}
            tick={currentTick}
            busy={busyConsumable}
            blockedReason={consumableBlockedReason}
            onUse={useConsumable}
          />
          </> : null}
          {branch && !branch.complete ? (
            <p className="ka-hud-resolving" data-generated-branch-pending={branch.jobId ?? "unavailable"}>
              {branch.note ?? "the rest of this branch is still resolving"}
            </p>
          ) : null}
          <div className="ka-hud-controls" data-generated-controls>
            <Button size="sm" disabled={busyConsumable !== null} onClick={() => { setVerdictDismissed(false); setPlaying((value) => !value); }} data-action="toggle-play" data-playing={playing}>
              {busyConsumable !== null ? "Applying…" : playing ? "Pause" : "Play"}
            </Button>
            <Button size="sm" variant="outline" disabled={busyConsumable !== null} onClick={() => { setStep(0); setPlaying(false); setVerdictDismissed(false); }} data-action="restart">
              Restart
            </Button>
            <Button size="sm" variant="outline" disabled={busyConsumable !== null} onClick={() => { setPlaying(false); setStep((s) => Math.max(0, s - 1)); }} data-action="step-back">
              ◀ Back
            </Button>
            <Button size="sm" variant="outline" disabled={busyConsumable !== null} onClick={() => { setPlaying(false); setStep((s) => Math.min(lastStep, s + 1)); }} data-action="step-forward">
              Next ▶
            </Button>
            <input
              type="range"
              min={0}
              max={lastStep}
              value={Math.min(step, lastStep)}
              onChange={(event) => { setPlaying(false); setStep(Number(event.target.value)); }}
              disabled={busyConsumable !== null}
              className="ka-hud-range"
              data-action="timeline"
            />
            <span
              className="font-mono text-[10px] text-muted-foreground"
              data-generated-clock
              title="the replay clock: 1 frame = 1 tick = 50 ms"
            >
              frame {frame?.index ?? 0}/{lastStep} · tick {currentTick}
            </span>
            <span className="flex items-center gap-1" data-generated-speed={speed}>
              {[0.5, 1, 2, 4].map((value) => (
                <Button
                  key={value}
                  size="sm"
                  variant={speed === value ? "default" : "outline"}
                  onClick={() => setSpeed(value)}
                  data-action={`speed-${value}`}
                >
                  {value}x
                </Button>
              ))}
            </span>
          </div>
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground" data-generated-stage-fallback>
        {stageFallbackNote}
      </p>

      {/* Feedback for the last item click: what the runner changed, printed from its own acceptance. */}
      {branch ? (
        <div className="space-y-1 rounded-md border border-emerald-500/40 bg-emerald-500/5 px-3 py-2 text-xs" data-generated-branch-changes>
          {branch.result.acceptance.changed.length === 0 ? (
            <p className="text-muted-foreground">The runner reported no parameter change for this use.</p>
          ) : (
            branch.result.acceptance.changed.map((change) => (
              <p key={`${change.unitId}-${change.seq}`} data-generated-branch-change={change.unitId ?? "unknown"}>
                {nameOfUnit(change.unitId)}{" "}
                {change.parameter === 10 ? "HP" : change.parameter === 11 ? "MP" : `param ${change.parameter}`}{" "}
                {change.before} → {change.after} (max {change.max})
              </p>
            ))
          )}
          <p className="text-muted-foreground">
            Stock now {branch.result.acceptance.stock.after}/{branch.result.acceptance.stock.declared}
            {branch.result.acceptance.stock.spent ? " (the runner spent one)" : " (nothing was spent)"}.
          </p>
        </div>
      ) : null}

      {/*
       * The secondary panels are collapsed by default. The compact HUD above already carries the live
       * chest strip and the tick line; these hold the fuller lists for anyone who wants to read them.
       */}
      <div className="grid gap-3 lg:grid-cols-2">
        <details className="ka-panel" data-generated-chest-card>
          <summary className="ka-panel__summary">
            Chests
            <span className="ka-panel__hint">simulated drops - reward settlement not validated</span>
          </summary>
          <div className="ka-panel__body text-xs">
            <div className="flex flex-wrap items-center gap-2" data-generated-chest-drops-full>
              {visibleChestDrops.length === 0 ? (
                <span className="text-muted-foreground">
                  {atFinalTick
                    ? "No chest dropped in this fight: the queued count is set by the rival leader leaving the field."
                    : "No chest queued yet at this tick."}
                </span>
              ) : (
                visibleChestDrops.slice(0, 12).map((drop) => (
                  <span
                    key={drop.seq}
                    className="flex items-center gap-1 rounded border px-1.5 py-0.5"
                    data-generated-chest={drop.treasureId ?? "unresolved"}
                  >
                    <ChestSprite icon={drop.icon} name={drop.name} />
                    <span>
                      {drop.name ?? "unresolved box"} · tick {drop.tick}
                    </span>
                  </span>
                ))
              )}
            </div>
            {visibleChestDrops.length > 12 ? (
              <p className="mt-1 text-muted-foreground" data-chest-overflow-note>
                + {visibleChestDrops.length - 12} more queued chests
              </p>
            ) : null}
            {/*
             * Mid-fight the panel counts what the runner has already queued and claims nothing about the
             * result. The queue / entitlement summary, its explanation and the verdict appear at the
             * end of the fight, where the runner's finish payload actually exists.
             */}
            {atFinalTick ? (
              <>
                <p className="mt-2">
                  Queued by the end of the fight:{" "}
                  <span data-generated-chest-queued={chestSummary.queued}>{chestSummary.queued}</span>
                  {" · "}
                  eligible in simulation:{" "}
                  <span data-generated-chest-awarded={chestSummary.awarded === null ? "unknown" : String(chestSummary.awarded)}>
                    {chestSummary.awarded === null ? "unknown" : chestSummary.awarded}
                  </span>
                </p>
                <p className="text-muted-foreground" data-generated-chest-note>
                  {chestSummary.note}
                </p>
                <p data-generated-outcome-summary className="text-muted-foreground">
                  {outcomeWord}
                  {outcome.censored ? " · incomplete simulation record" : ""} · battle result{" "}
                  {rewardSummary?.battleResult ?? outcomeWord} · victory required: {rewardSummary?.victoryRequired ?? "unknown"}
                </p>
              </>
            ) : (
              <p className="mt-2 text-muted-foreground" data-generated-chest-provisional>
                Queued so far at tick {currentTick}:{" "}
                <span data-generated-chest-queued={chestsSoFar}>{chestsSoFar}</span>
                {chestsSoFar === 1 ? " chest" : " chests"} · provisional until the runner's result; the full
                summary appears when the fight ends.
              </p>
            )}
          </div>
        </details>

        <details className="ka-panel" data-generated-skill-releases-card>
          <summary className="ka-panel__summary">
            Skills used ({visibleSkillReleases.length})
            <span className="ka-panel__hint">released by tick {currentTick}</span>
          </summary>
          <div className="ka-panel__body max-h-56 space-y-1 overflow-auto text-xs" data-generated-skill-releases>
          {visibleSkillReleases.length === 0 ? (
            <p className="text-muted-foreground">
              {atFinalTick ? "No skill was released in this fight." : "No skill released yet at this tick."}
            </p>
          ) : (
            visibleSkillReleases.slice(0, 60).map((release) => {
              const invocation = generatedReleaseInvocation(release, units);
              return (
                <div key={release.seq} className="flex items-center gap-2" data-skill-release={release.skillId}>
                  {release.icon ? (
                    <img src={release.icon} alt="" className="h-4 w-4 [image-rendering:pixelated]" />
                  ) : (
                    <span className="h-4 w-4 rounded-sm border border-dashed border-muted-foreground/60" />
                  )}
                  <span className="font-medium">{release.name ?? `skill ${release.skillId}`}</span>
                  <span className="text-muted-foreground">
                    {nameOfUnit(release.casterUnitId)} · tick {release.tick} · slot {release.slot ?? "-"}
                    {invocation === null ? "" : ` · invocation ${invocationLabel(invocation)}`}
                  </span>
                </div>
              );
            })
          )}
          </div>
        </details>
      </div>

      {placeholderCount > 0 || renderTally.loadoutPreview > 0 ? (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs" data-generated-art-warning>
          {missingMonsterArtCount > 0
            ? `${missingMonsterArtCount} fighter${missingMonsterArtCount === 1 ? " has" : "s have"} no recovered battle sheet and ${missingMonsterArtCount === 1 ? "keeps" : "keep"} a labelled placeholder. `
            : ""}
          {missingAppearanceCount > 0
            ? `${missingAppearanceCount} fighter${missingAppearanceCount === 1 ? " has" : "s have"} neither appearance inputs nor a job identity, so ${missingAppearanceCount === 1 ? "it keeps" : "they keep"} a labelled placeholder. `
            : ""}
          {renderTally.loadoutPreview > 0
            ? `${renderTally.loadoutPreview} fighter${renderTally.loadoutPreview === 1 ? " is" : "s are"} drawn by the shared loadout renderer from their own job, rank, gender and equipment - not a placeholder.`
            : ""}
        </p>
      ) : null}

      <details className="rounded-lg border bg-card/40 px-4 py-3" data-generated-details>
        <summary className="cursor-pointer text-sm font-medium">
          Details &amp; diagnostics
          <span className="ml-2 text-[11px] font-normal text-muted-foreground">
            every unit's numbers, the replay's own event log, coverage and reward fields
          </span>
        </summary>
        <div className="mt-3 space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm">What happens at tick {currentTick}</CardTitle>
                <CardDescription>
                  Every line is one authoritative replay event at this tick, printed from the runner's own
                  fields. The tag groups damage / heal / status / death / prize / recovered action clips; a
                  kind this view does not know is shown raw instead of guessed.
                </CardDescription>
              </CardHeader>
              <CardContent className="max-h-56 space-y-1 overflow-auto font-mono text-[10px]" data-generated-event-explanations>
                {frame?.events.length ? (
                  frame.events.map((event, index) => (
                    <div
                      key={`${event.seq}-${event.kind}-${index}`}
                      data-event-explanation={generatedEventGroup(event.kind)}
                    >
                      <span className="mr-1 rounded bg-muted px-1 text-[9px] uppercase text-muted-foreground">
                        {generatedEventGroup(event.kind)}
                      </span>
                      {explainGeneratedEvent(event, nameOfUnit)}
                    </div>
                  ))
                ) : (
                  <p className="text-muted-foreground">empty tick: no events applied</p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm">Outcome &amp; rewards (runner finish / finalState)</CardTitle>
                <CardDescription>
                  Read verbatim from the payload; an absent block is reported as absent instead of guessed.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-1 text-xs" data-generated-final-totals
                data-generated-finish={outcome.finish ? "present" : "finalState-only"}>
                <p data-generated-inventory>Runner inventory-collection record: {outcome.inventory}</p>
                <p>prize callbacks: {outcome.prizeCallbacks}</p>
                <p>stop: {outcome.stopReason ?? "-"}{outcome.censored ? " · censored" : ""}</p>
                <p data-generated-run-ticks>runner ticks: {outcome.ticks}</p>
                {/*
                 * The playback cut vs the declared horizon (SHIP-RUNTIME-CONTRACT.md section 2). Present
                 * whenever the deployed runtime is the rebuilt one; a legacy payload keeps its fallback.
                 */}
                <p data-generated-ending-cut>
                  playback end (endingCutTick):{" "}
                  {displayedReplay.finalState.endingCutTick ?? "legacy - last event tick"} · verdict tick:{" "}
                  {verdictTick ?? "-"} · horizon: {displayedReplay.finalState.horizonTick ?? outcome.ticks - 1} · kept
                  departure window: {displayedReplay.finalState.postEndingTicks ?? "-"} · silent tail:{" "}
                  {displayedReplay.finalState.silentTailTicks ?? "unknown"}
                </p>
                {displayedReplay.finalState.endingCutReason ? (
                  <p className="text-muted-foreground" data-generated-ending-cut-reason>
                    {displayedReplay.finalState.endingCutReason}
                  </p>
                ) : null}
                {/* native queue / pre-result eligibility / settled are three different states (section 3) */}
                <p data-generated-chest-state>
                  chests - queued {chestSummary.queued} · eligible{" "}
                  {chestSummary.awarded === null ? "unknown" : chestSummary.awarded}
                  {awardedBasis ? ` (${awardedBasis})` : ""} · settled: not claimed by this payload
                </p>
                {outcome.finish ? (
                  <div className="font-mono text-[10px]">
                    <p data-generated-finish-chests>queued chests: {formatUnknown(outcome.finish.queuedChestAwards)}</p>
                    <p data-generated-finish-exp>exp: {formatUnknown(outcome.finish.exp)}</p>
                    {outcome.postFinish ? (
                      <p data-generated-post-finish>post-finish: {formatUnknown(outcome.postFinish)}</p>
                    ) : null}
                  </div>
                ) : (
                  <p className="text-muted-foreground" data-generated-finish-absent>
                    this payload has no finish block (older export); only the runner's finalState is shown
                  </p>
                )}
                {units.map((unit) => {
                  const final = displayedReplay.finalState.units.find((entry) => entry.unitId === unit.unitId);
                  const live = frame?.units[unit.unitId];
                  return (
                    <p key={unit.unitId} data-generated-final-unit={unit.unitId}>
                      {unit.side}:{unit.rosterIndex} {unit.name} · final HP {final?.hp ?? "-"}/{live?.maxHp ?? unit.maxHp ?? "?"} ·
                      MP {final?.mp ?? "-"}/{live?.maxMp ?? unit.maxMp ?? "?"} · state{" "}
                      {final?.stateName ?? final?.state ?? "-"} · damage taken{" "}
                      {totals.damage.get(unit.unitId) ?? 0} · healed {totals.healing.get(unit.unitId) ?? 0}
                    </p>
                  );
                })}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Allies ({allies.length})</CardTitle>
                <CardDescription>Order and cells come from the replay, not from a fixture.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {allies.map((unit) => (
                  <UnitRow key={unit.unitId} unit={unit} frame={frame?.units[unit.unitId]} />
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Enemies ({enemies.length})</CardTitle>
                <CardDescription>Roster, order and formation are the simulator's.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {enemies.map((unit) => (
                  <UnitRow key={unit.unitId} unit={unit} frame={frame?.units[unit.unitId]} />
                ))}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Coverage</CardTitle>
                <CardDescription>Visual warnings only; they never change simulator results.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-1 text-xs" data-generated-coverage>
                <p data-coverage-arena>ARENA {coverage.arenaPartial ? "PARTIAL" : "FULL"}</p>
                <p data-coverage-monster-art>
                  MONSTER ART {coverage.enemyArtPartial ? `PARTIAL (${coverage.missingMonsterArt.length} units)` : "FULL"}
                </p>
                <p data-coverage-human-appearance>
                  HUMAN APPEARANCE {coverage.partialHumanAppearance.length ? `PARTIAL (${coverage.partialHumanAppearance.join(", ")})` : "FULL"}
                </p>
                <p data-coverage-weapon-clip>
                  WEAPON CLIP {coverage.undecodedWeaponClips.length ? `PARTIAL (motions ${coverage.undecodedWeaponClips.join(", ")})` : "FULL"}
                </p>
                <p data-coverage-skill>
                  SKILL ANIMATION NOT YET RECOVERED ({coverage.skillAnimationPending} events, metadata preserved)
                </p>
                {coverage.warnings.length ? (
                  <p className="font-mono text-[10px] text-muted-foreground" data-coverage-setup-warnings>
                    setup warnings: {coverage.warnings.join(", ")}
                  </p>
                ) : null}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Skill events ({visibleSkillEvents.length})</CardTitle>
                <CardDescription>
                  Preserved for the dedicated skill-animation pass; no generic animation is played. Shown up to the
                  displayed tick, so a not-yet-reached release never appears early.
                </CardDescription>
              </CardHeader>
              <CardContent className="max-h-56 space-y-1 overflow-auto font-mono text-[10px]" data-generated-skill-events>
                {visibleSkillEvents.slice(0, 40).map((event) => (
                  <div key={event.seq} data-skill-event={event.skillId}>
                    seq {event.seq} · tick {event.tick} · {event.actorUnitId}→{event.targetUnitId} · skill{" "}
                    {event.skillId} · slot {event.skillSlot ?? "-"} · motion {event.motion ?? "-"} · inv{" "}
                    {event.invocationLevel ?? "-"}
                  </div>
                ))}
                {visibleSkillEvents.length === 0 ? <p className="text-muted-foreground">no skill event up to this tick</p> : null}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Items available at tick {currentTick}</CardTitle>
              <CardDescription>
                Supporting detail for the in-frame action bar. A row the recovered battle touch path cannot
                reach keeps its technical text collapsed; the player-facing reason stays on the button.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-xs" data-generated-consumable-detail>
              {consumableRows.map((row) => (
                <div
                  key={row.key}
                  className="rounded-md border border-border/60 px-2 py-1.5"
                  data-consumable-detail={row.key}
                  data-consumable-detail-usable={row.usableAtTick ? "1" : "0"}
                  data-consumable-detail-supported={row.supported ? "1" : "0"}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{row.label}</span>
                    <span className="text-muted-foreground">
                      {row.parameterName ? `${row.parameterName} · ` : ""}stock {row.stockAtTick}/{row.declaredStock}
                    </span>
                  </div>
                  <p className="mt-0.5 text-muted-foreground" data-consumable-detail-reason>
                    {!row.supported
                      ? `${CONSUMABLE_UNSUPPORTED_TEXT}.`
                      : row.usableAtTick
                        ? `Usable at tick ${currentTick} - ${row.usableReason}.`
                        : row.usableReason}
                  </p>
                  {row.technicalReason ? (
                    <details className="mt-1" data-consumable-technical>
                      <summary className="cursor-pointer text-[10px] text-muted-foreground">Technical detail</summary>
                      <p className="mt-1 font-mono text-[10px] text-muted-foreground" data-consumable-technical-text>
                        {row.technicalReason}
                      </p>
                    </details>
                  ) : null}
                </div>
              ))}
              {consumableBlockedReason ? (
                <p className="text-muted-foreground" data-consumable-blocked-reason>
                  {consumableBlockedReason}
                </p>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </details>
    </div>
  );
}
