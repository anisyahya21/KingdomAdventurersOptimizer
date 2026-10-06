import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  CircleHelp,
  Crosshair,
  Download,
  Eye,
  FilePlus2,
  FlaskConical,
  FolderOpen,
  Layers,
  Pause,
  Play,
  RefreshCw,
  Square,
  Swords,
  Upload,
  X,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PageHeader } from "@/components/ka/page-header";
import { ToneBadge } from "@/components/ka/badges";
import { GeneratedBattleReplay } from "@/components/generated-battle-replay";
import { parseBattleReplayResult, type ReplayVisualSetup } from "@/lib/battle-replay-result";
import { createGeneratedBattleRecord, type GeneratedBattleRecord } from "@/lib/generated-battle-store";
import { cn } from "@/lib/utils";
import { getSkillIcon } from "@/lib/skill-icons";
import { statIconKey } from "@/lib/stat-icons";
import { OutcomeHistogram, StrategyFormationPanel, type OutcomeDistribution, type StrategyFormation } from "@/components/ka/strategy-insights";
import "./strategy-optimizer-workspace.css";
import type { KaCategory } from "@/design-system/category-styles";
import {
  canonicalStatKey,
  PARAMETER_NATIVE_NAMES,
  PARAMETER_STAT_KEYS,
  STAT_PARAMETER_IDS,
  type StatKey,
} from "@/game-data/stat-parameter-ids";
import {
  ADVANCED_PARAMETER_KEYS,
  frozenInvestigationScenario,
  parameterReading,
  slotParameterView,
  summarizeTrialBatch,
  teamBuildSlots,
  type BatchRunReading,
  type HostBankSummary,
  type TrialBatchSummary,
  type TeamBuildSlot,
} from "@/lib/strategy-team-build";
import {
  createRecordHighlightTracker,
  RECORD_HIGHLIGHT_MS,
  recordCellKey,
  trackRecordHighlights,
  type RecordHighlightState,
  type RecordHighlightTracker,
  type RecordReadings,
} from "@/lib/strategy-record-highlights";
import EncounterOptimizerPanel from "@/components/optimizer-operating-regions";
import OptimizerPlayerRegions from "@/components/optimizer-player-regions";
import type { EncounterAwareSnapshot, EncounterChoice } from "@/types/encounter-optimizer";
import type { PlayerRegionsSummary } from "@/types/player-regions";

/* ------------------------------------------------------------------ */
/* Host bridge contract                                                */
/* ------------------------------------------------------------------ */

/**
 * The desktop host (`window.pywebview.api`) is the only interface this page has. Astra's Python
 * layer owns every mechanic, score, default and scheduler decision; this module declares the exact
 * shapes it is handed and renders them. It never reproduces a metric or invents a game fact.
 */

type SeedPair = [number, number];

type Summary = {
  n: number;
  wins: number;
  losses: number;
  censored: number;
  winRate: number | null;
  winInterval: [number, number];
  failureRate: number | null;
  unresolvedRate: number | null;
  /**
   * Mean retained chests per resolved run. The runner currently reports null because automatic
   * Finish and reward collection are not modelled, and the page renders that as unknown rather
   * than zero. A future runner that exposes a real number flows through unchanged.
   */
  retainedMean: number | null;
  /** How many runs actually contributed a retained sample, when the runner reports it. */
  retainedSampleCount?: number | null;
  /**
   * Chests released per resolved run, using the run rule in `chestReading`: 0 on a loss (the
   * recorded native dispatch gate), the awarded or queued count at victory. Null until a resolved
   * run exists. This is what the fight produced, not an inventory receipt.
   */
  chestMean?: number | null;
  chestSampleCount?: number | null;
  chestMin?: number | null;
  chestMax?: number | null;
  /** Spread of the per-run chest counts and the standard error of the mean above. */
  chestSD?: number | null;
  chestSE?: number | null;
  callbackMean: number | null;
  callbackSD: number | null;
  callbackMin: number | null;
  callbackP10: number | null;
  callbackMax: number | null;
  callbackHistogram?: Record<string, number>;
  meanResources: number | null;
  meanSurvivors: number | null;
  meanTicks: number | null;
  comparable: boolean;
};

/**
 * One stored `interesting()` run: the runner's compact metric dict. Every field is read as data
 * only. `verdict` 1 = win and 2 = loss; `censored` means unresolved at the horizon. `retained` is
 * the runner's own retained-chest count when it exists, never a prize-callback count.
 */
type Example = {
  seeds?: number[];
  digest?: string;
  verdict?: number | null;
  censored?: boolean | null;
  retained?: number | null;
  /** Chests this run released, and the basis the runner used to say so. */
  chests?: number | null;
  chestBasis?: string | null;
  prizeCallbacks?: number | null;
  survivors?: number | null;
  resourceUses?: number | null;
  ticks?: number | null;
  rewardOutcome?: RewardOutcome | null;
};

/**
 * The runner's own reward-entitlement reading, exposed under `rewardOutcome`. `awardedChests` is
 * the native value: 0 from the win/loss gate on a loss, the certified pre-verdict pending count on
 * a certified win, and null on an unresolved run or a win without a certificate. A certified award
 * is an *entitlement*, not a retained chest - `inventoryVerified` stays false until automatic
 * Finish and world collection are proven, and a pending count or callback is never a chest count.
 */
type RewardOutcome = {
  pendingChests?: number | null;
  awardedChests?: number | null;
  awardedBasis?: string | null;
  inventoryVerified?: boolean;
  reason?: string | null;
  certificate?: {
    certificateId?: string | null;
    holds?: boolean;
    frame?: number | null;
    issuedBeforeVerdict?: boolean;
    timingRule?: string | null;
  } | null;
};

type FighterStat = {
  monster?: boolean;
  weaponId?: number | string | null;
  weaponRange?: number | null;
  effectiveDefense?: number | null;
  averageTrainingLevel?: number | null;
  parameters?: Record<string, { value: number | null; maximum: number | null }>;
  skillCosts?: Array<{ skillId: number; cost: number }>;
};

type Candidate = {
  id: string;
  label: string;
  source: string;
  scenario: unknown;
  stats: Record<string, FighterStat>;
  discovery: Summary;
  validation: Summary;
  selection: Summary;
  validationRuns: number;
  family: string | null;
  /** Concrete differences from this encounter's supplied baseline (presentation only). */
  changes?: string[];
  /** This candidate's own records: best defeat opportunity and best winning chest count. */
  bestPotentialChests?: number | null;
  bestChestsEarned?: number | null;
  encounterId?: number;
  examples: Record<string, Example>;
};

type ArchiveRow = { cell: string; candidate: string; quality: string };

type Provenance = {
  digest?: string;
  mode?: string;
  count?: number;
  files?: Record<string, string | null>;
  missing?: string[];
};

/**
 * What the bulk workers will actually run. The host enables the exact accelerators only when a
 * recorded parity report agrees with the current engine snapshot, so a changed simulator reports
 * `enabled: false` with the reason instead of silently changing the numbers.
 */
type Acceleration = {
  enabled?: boolean;
  modules?: string[];
  reason?: string | null;
  digest?: string | null;
  recordedAt?: string | null;
};

/**
 * The fight behind a scenario, read from the engine's own encounter data. A run's `encounterId`
 * alone is not enough to know what was fought, so the host also supplies the recovered title, the
 * boss and the roster size. Every field may be absent, and the UI then shows the bare number.
 */
type EncounterInfo = {
  title?: string | null;
  boss?: string | null;
  enemyCount?: number | null;
  distinctFollowerCount?: number | null;
  level?: number | null;
};

/**
 * One paired comparison the host computed against the reference strategy at a probed value. Every
 * field is read as data; null (too few shared seeds to pair) is rendered as such and never filled in.
 */
type ProbeDelta = {
  mean?: number | null;
  se?: number | null;
  t?: number | null;
  lower?: number | null;
  upper?: number | null;
  n?: number | null;
};

/**
 * One measured rung of a probe ladder. `effective` (and `effective2` on a grid) is the stat the fight
 * actually used, which is not always the probed value; both are shown, neither is recomputed here.
 */
type ProbePoint = {
  value?: number | null;
  value2?: number | null;
  effective?: number | null;
  effective2?: number | null;
  candidateId?: string | null;
  runs?: number | null;
  chestMean?: number | null;
  chestSE?: number | null;
  viable?: boolean | null;
  delta?: ProbeDelta | null;
};

/** A per-row threshold on a grid probe, keyed by the first axis value. */
type ProbeThreshold = {
  threshold?: number | string | null;
  bracketed?: boolean | null;
  failsAt?: number | string | null;
  note?: string | null;
};

/** The host's demonstrated interaction between two axes on a grid, when one was shown at all. */
type ProbeInteraction = {
  kind?: string | null;
  axis?: string | null;
  other?: string | null;
  fromValue?: number | string | null;
  fromThreshold?: number | string | null;
  fromBracketed?: boolean | null;
  toValue?: number | string | null;
  toThreshold?: number | string | null;
  toBracketed?: boolean | null;
  difference?: number | string | null;
  gridStep?: number | string | null;
};

/** One published threshold/range analysis: a single-axis ladder, or a two-axis grid. */
type Probe = {
  kind?: "grid";
  pivotId: string;
  pivotLabel?: string | null;
  axis: string;
  axisLabel?: string | null;
  axis2?: string | null;
  axis2Label?: string | null;
  unit?: string | null;
  referenceRuns?: number | null;
  viableLow?: number | null;
  viableHigh?: number | null;
  boundaryAt?: string | null;
  statement?: string | null;
  basis?: string | null;
  points?: ProbePoint[] | null;
  thresholds?: Record<string, ProbeThreshold> | null;
  interaction?: ProbeInteraction | null;
};

/** A value the most recent probe command turned into (or reused as) a candidate build. */
type ProbeRung = {
  value?: number | string | null;
  value2?: number | string | null;
  candidateId?: string | null;
};

/**
 * What the most recent `probe` command did. `established: false` means the user asked for an axis the
 * optimiser does not hold as a combat input, with `axisNote` as the host's reason.
 */
type LastProbe = {
  axis?: string | null;
  axis2?: string | null;
  unit?: string | null;
  pivotId?: string | null;
  pivotLabel?: string | null;
  runs?: number | null;
  ladder?: Array<number | string> | null;
  ladder2?: Array<number | string> | null;
  established?: boolean | null;
  axisNote?: string | null;
  created?: ProbeRung[] | null;
  reused?: ProbeRung[] | null;
  refused?: Array<ProbeRung & { reason?: string | null }> | null;
};

/**
 * The per-point evidence a fine-tuning program publishes for every tested point (or grid cell),
 * exactly as the host's `strategy_finetune.summarise_rows` builds it. `resolved` and `runs` are the
 * point's own sample counts (unresolved runs are never replaced by zero), `minSeeds` is the program's
 * readiness floor, `paired` is the paired difference against the unchanged frozen parent over the
 * seed ordinals both measured, and `viable` the paired non-inferiority verdict. A null mean is
 * genuinely unmeasured and is rendered as such.
 */
type FineTuneEvidence = {
  runs?: number | null;
  resolved?: number | null;
  wins?: number | null;
  losses?: number | null;
  unresolved?: number | null;
  winRate?: number | null;
  winInterval?: [number, number] | null;
  meanEarned?: number | null;
  earnedSamples?: number | null;
  earnedSE?: number | null;
  meanPotential?: number | null;
  potentialSamples?: number | null;
  chestMean?: number | null;
  chestSE?: number | null;
  chestMin?: number | null;
  chestMax?: number | null;
  paired?: ProbeDelta | null;
  viable?: boolean | null;
  ready?: boolean | null;
  comparable?: boolean | null;
};

/** One measured point of a fine-tuning program: the requested target and the effective stat used. */
type FineTunePoint = FineTuneEvidence & {
  value?: number | null;
  effective?: number | null;
  candidateId?: string | null;
  bank?: number | null;
  reason?: string | null;
};

/** One measured cell of the two-axis interaction grid: both requested targets live here. */
type FineTuneCell = FineTuneEvidence & {
  value?: number | null;
  value2?: number | null;
  candidateId?: string | null;
  bank?: number | null;
};

/** The measured bracket between a working point and a failing point, with the probed spacing. */
type FineTuneBoundary = {
  low?: number | null;
  high?: number | null;
  resolution?: number | null;
  bracketed?: boolean | null;
};

/** The interaction reading the host publishes once a single-axis boundary is bracketed. */
type FineTuneGrid = {
  axis2?: string | null;
  statement?: string | null;
  interaction?: ProbeInteraction | null;
};

/**
 * One fine-tuning program, exactly as the optimizer publishes it (`_finetune_view`). A program
 * freezes one parent build and varies exactly one combat statistic at a time, so `axis`/`unit`/
 * `baseline` identify the swept input and every `points`/`cells` row is attributable to it. `parent`
 * is the frozen build the program is grouped under; `auto` marks a program automatic tuning started.
 */
type FineTuneProgram = {
  id: string;
  status: string;
  auto?: boolean;
  axis: string;
  axisLabel?: string | null;
  axis2?: string | null;
  unit?: string | null;
  encounterId: number;
  parent: { candidateId: string; label?: string | null };
  baseline?: { effective?: number | null; input?: unknown } | null;
  direction?: string | null;
  step?: number | null;
  targets?: number[] | null;
  budget?: Record<string, unknown> | null;
  used?: { children?: number; points?: number; interactionCells?: number } | null;
  focused?: boolean;
  dispatchable?: boolean;
  waitingForFocus?: boolean;
  minSeeds?: number | null;
  boundary?: FineTuneBoundary | null;
  resolved?: boolean;
  viableLow?: number | null;
  viableHigh?: number | null;
  statement?: string | null;
  basis?: string | null;
  grid?: FineTuneGrid | null;
  points?: FineTunePoint[] | null;
  cells?: FineTuneCell[] | null;
};

/** One planned axis of the host's automatic-tuning diagnostic, published per focused encounter. */
type AutoTuneAxis = {
  axis: string;
  axisLabel?: string | null;
  unit?: string | null;
  baseline?: number | null;
  reason?: string | null;
  programId?: string | null;
  status?: string | null;
  points?: number;
  runs?: number;
  children?: number;
  maxChildren?: number | null;
  waitingForFocus?: boolean;
};

/**
 * The host's automatic-tuning diagnostic for the focused fight: which parent it selected, which axes
 * it started, and `reason`, the one-line explanation for a program that is not running. It is the
 * host's own reading and the page only ever states it, never recomputes readiness.
 */
type AutoTune = {
  encounterId?: number | null;
  status?: string | null;
  parent?: {
    candidateId?: string | null;
    label?: string | null;
    meanEarned?: number | null;
    resolved?: number | null;
    wins?: number | null;
    n?: number | null;
  } | null;
  plannedAxes?: AutoTuneAxis[] | null;
  startedAxes?: string[] | null;
  leaderChanged?: boolean | null;
  reason?: string | null;
};

type EncounterDifficulty = {
  encounterId: number;
  /** The real difficulty/stage name from the recovered catalogue. */
  label: string;
  title: string;
  level?: number | null;
  enemyCount?: number | null;
  boss?: string | null;
};

type EncounterCampaign = {
  key: string;
  title: string;
  boss?: string | null;
  difficulties: EncounterDifficulty[];
};

/**
 * One encounter/difficulty's stored lifetime record, exactly as the coordinator persists it. The
 * `lifetime*` names are the authoritative fields: they are written inside the same transaction as
 * each run and survive candidate pruning, while an overview that recomputed "attempts" from the
 * surviving rows would fall to 0 as soon as a candidate was replaced. The two maxima are taken over
 * resolved runs only, so an unresolved (no-verdict) run can neither supply a winning chest count nor
 * a defeated opportunity.
 */
type EncounterStat = {
  /** Lifetime attempts, split by outcome; `lifetimeNoVerdict` is "no verdict by limit", never a loss. */
  lifetimeAttempts: number;
  lifetimeWins: number;
  lifetimeLosses: number;
  lifetimeNoVerdict: number;
  highestPotentialChests: number | null;
  highestChestsEarned: number | null;
  earnedBasis?: string | null;
  potentialBasis?: string | null;
  reconstructedAttempts?: number;
  ea?: {
    highestChestsEarned?: number | null;
    highestPotentialChests?: number | null;
    earnedCandidateId?: string | null;
    potentialCandidateId?: string | null;
  };
};

/**
 * The four ways a build can still be worth keeping. They are lanes, not weights: each one orders the
 * population by its own signal, so a 99-potential defeat, a stored-attack setup with no reward yet,
 * and a cheaper build with the same chests all survive alongside the highest-earning strategy.
 *
 * The potential lane orders by the prize a build has **not yet cashed** (reached minus earned), not by
 * the raw potential it once reached. Raw potential is a lifetime maximum that never falls, so ordering
 * by it parked the pool on whichever build had ever got furthest - measured on a live library, a build
 * with 0 wins and 0 earned in 88 runs was leading it while the fight's actual earner won 88 of 88.
 */
type LaneName = "earned" | "potential" | "setup" | "efficiency";

type LaneLeader = {
  candidate: string;
  label: string;
  source: string;
  metrics: {
    earned: number | null;
    potential: number | null;
    resources: number | null;
    winRate: number | null;
    winLower: number | null;
    runs: number;
    /** Stored-attack progress counters of the best run this build had (see the setup lane). */
    progress: Record<string, number>;
    progressRuns: number;
  };
  members: number;
  pool: number;
  validated: boolean;
};

/**
 * A lane's leader stated as the lane measures it. The setup lane names the causal chain - post-death
 * prizes produced by stored attacks released after the boss died, and the mass of stored attacks
 * pointed at the boss when it died - because that is the chain the mechanism was traced through.
 */
function laneReading(lane: LaneName, leader: LaneLeader): string {
  const metrics = leader.metrics;
  const progress = metrics.progress ?? {};
  if (lane === "earned") return `${formatInt(metrics.earned)} chests earned on a win`;
  if (lane === "potential") {
    // This pool ranks by the prize not yet cashed, so the reading leads with that gap. Showing the raw
    // potential first would put a number on screen that is not the number the pool ordered by.
    const reached = metrics.potential ?? 0;
    const earned = metrics.earned ?? 0;
    return `${formatInt(Math.max(0, reached - earned))} reached but not earned · ` +
      `${formatInt(reached)} reached · ${formatInt(earned)} earned`;
  }
  if (lane === "setup")
    return `${formatInt(progress.postDeathPrizes ?? 0)} post-death prizes from ` +
      `${formatInt(progress.commandsReleasedAfterDeathTargetingBoss ?? 0)} boss-targeted releases ` +
      `after the boss died · ${formatInt(progress.storedCommandsTargetingBossAtDeath ?? 0)} stored at death`;
  return `${formatInt(metrics.earned)} chests at ${formatNumber(metrics.resources, 2)} consumables/battle`;
}

type FocusedExperiment = {
  id: string; mode: string; status: string; candidateId: string; encounterId: number;
  completed: number; total: number; requestedPerBuild: number; createdAt?: number;
  endedAt?: number;
  comparisons?: Array<{candidateId: string; label: string; runs: number; meanEarned: number | null;
    earnedSamples?: number; wins?: number; losses?: number; unresolved?: number;
    paired?: {n: number; mean: number; lower: number; upper: number} | null}>;
};
type OptimizerStatus = {
  statusTransport?: { generatedAtUnix?: number; ageSeconds?: number;
    refreshing?: boolean; lastError?: string | null };
  nativeProgress?: { completed?: number; waveCompleted?: number; waveTotal?: number | null };
  focusedExperiment?: FocusedExperiment | null;
  state: string;
  error: string | null;
  compatible: boolean;
  /**
   * The students' split of the pool, published with every snapshot. A setting rather than a candidate:
   * it decides what is dispatched next, so it can never reach a battle or invalidate a library.
   * `report` is the last explicit measurement of runs per student and is null until one is asked for,
   * because reading it costs a full pass over the evidence table.
   */
  students?: {
    shares?: Record<string, number>;
    warning?: string | null;
    report?: Record<string, { candidates?: number; encounters?: number; runs?: number }> | null;
    /**
     * The rebellious cheater's rule-break ledger, read with `report` on the same request. Keyed
     * `T<version> -> <rules broken> -> reading`, so a line says which distance was tested and what it
     * measured. Absent until "Refresh counts" has been asked for, because it costs an evidence pass.
     */
    ledger?: Record<
      string,
      Record<string, { candidates?: number; runs?: number; bestMeanChests?: number }>
    > | null;
    /**
     * The tier table, published with every snapshot. A tier is a *distance*: which community rules it
     * may break, and how many at once. T1 breaks one, T4 all but one.
     */
    tiers?: { name: string; rules: string[]; minimum: number; maximum: number }[] | null;
    /**
     * Student 1's tracks, published with every snapshot. A track is a named line with its own share of
     * Student 1's budget: one per value, one that tests every pair of values, one that moves them
     * together along the dial. The one-axis tracks are what make "only Attack" a real configuration
     * rather than a hope, and the pair track is the only line that can reach a compensating pair at all.
     */
    tracks?: { name: string; kind: string; title: string; stats: string[];
               share: number }[] | null;
    /** How much of its share each track has actually been given. Read on demand, like `report`. */
    trackReport?: Record<string, { candidates?: number; runs?: number }> | null;
    /**
     * The pair track's coverage per encounter: `all` is every pair of values that fight has, `tested`
     * the ones actually recorded, `owed` the ones still unknown. "It has to know the relationship of
     * every pair" is a coverage claim, so it is published as one.
     */
    pairCoverage?: { encounter: number; all: string[]; tested: string[];
                     owed: string[] }[] | null;
    floor?: number;
  } | null;
  /**
   * The breakthrough / reliability stream: the mechanism-guided investigation. Published from the
   * optimiser's slow analysis pass, so it describes the last one rather than the current instant.
   * `encounters` is keyed by encounter id; each entry carries its own evidence status, the reward
   * distribution with its denominators, the hypothesis under test, the controlled arms and their
   * measured effects, and the last decision with its reason.
   */
  breakthrough?: {
    label?: string;
    source?: string;
    bands?: string[];
    thresholds?: number[];
    axes?: string[];
    axisParameters?: Record<string, number | null>;
    bank?: number;
    confirmBank?: number;
    cap?: number;
    /** What the campaign cap bounds: one encounter's campaign, not one experiment's or the whole library. */
    campaignScope?: string;
    spentRuns?: number;
    encounters?: Record<string, {
      /** The encounter's own name and difficulty, then its number: never a bare id. */
      label?: string;
      status?: string;
      updated?: number | null;
      lastDecision?: string;
      stats?: {
        n?: number; mean?: number | null; median?: number | null; p10?: number | null;
        maximum?: number | null; unresolved?: number;
        thresholds?: Record<string, { hits?: number; n?: number; rate?: number | null;
                                      wilson?: number[] }>;
      } | null;
      coverage?: { records?: number; candidates?: number; withTelemetry?: number;
                   withoutDetail?: number; unresolved?: number } | null;
      bands?: Record<string, { count?: number; kept?: number }>;
      examples?: Array<{ candidate?: string; label?: string; earned?: number; band?: string;
                         phase?: string; ordinal?: number; stream?: string }>;
      bottleneck?: { stage?: string; features?: string[] } | null;
      hypothesis?: {
        id?: string; statement?: string; status?: string; bottleneck?: string; basis?: string;
        features?: string[];
        supporting?: Array<{ candidate?: string; label?: string; phase?: string; ordinal?: number;
                             earned?: number }>;
        contrasting?: Array<{ candidate?: string; label?: string; phase?: string; ordinal?: number;
                              earned?: number }>;
        falsifier?: string;
        attempted?: Array<{ plan?: string; bank?: number }>;
      } | null;
      plan?: {
        id?: string; kind?: string; status?: string; bank?: number; developmentBank?: number;
        arms?: Array<{ name?: string; role?: string; axis?: string | null; value?: unknown;
                       n?: number; mean?: number | null; maximum?: number | null;
                       delta?: { mean?: number; lower?: number; upper?: number; n?: number } | null;
                       behaviourChange?: Record<string, number | null>;
                       rewardPromising?: boolean }>;
      } | null;
      confirmed?: Array<{ candidate?: string; delta?: { mean?: number; n?: number } | null }>;
      /**
       * The re-check of this encounter's published confirmations against their own frozen evidence.
       * A claim recorded before the frozen-window proof existed cannot show it, so it is withdrawn
       * from the badge and from selection and listed here with the reason.
       */
      claimsRevalidation?: {
        policy?: number;
        checked?: number;
        supported?: number;
        needsRevalidation?: number;
        withdrawn?: Array<{ candidate?: string; hypothesis?: string; confirmation?: string;
                            reason?: string }>;
      } | null;
      budget?: { runs?: number; plans?: number; experiments?: number; confirmations?: number };
      /**
       * The encounter's campaign budget, stated in full: its scope, cap, used work, the work its
       * in-flight plans have reserved (controls included), what remains, why it stopped, and the
       * explicit extension that resumes it. A cap is a pause, never a permanent stop.
       */
      campaign?: {
        scope?: string;
        cap?: number;
        used?: number;
        reserved?: number;
        remaining?: number;
        exhausted?: boolean;
        overCommitted?: boolean;
        /** Runs of this campaign's participants that its own records cannot attribute to it. */
        unattributedRuns?: number;
        stopReason?: string | null;
        extension?: string | null;
      } | null;
      inFlight?: boolean;
    }>;
  } | null;
  /**
   * The MP-recovery operation: noticing that a watched DPS or healer ran out of MP, and testing an
   * item-supported build against the observation. `setting` is the allowance the user controls —
   * with no declared stock and use cap nothing is created, and `awaitingAllowance` is how the surface
   * says so instead of appearing to work while creating no trials. `requests` are the corrections it
   * created, each with the parent's and the child's own results and the herb's own telemetry.
   */
  mpRecovery?: {
    label?: string;
    source?: string;
    thresholdPercent?: number;
    /** The wall-clock budget one coordinator pass may spend scanning, published as a revision stamp. */
    scanBudgetSeconds?: number;
    scanPerPass?: number;
    proposals?: number;
    awaitingAllowance?: boolean;
    refusals?: Record<string, string>;
    setting?: {
      enabled?: boolean;
      effective?: boolean;
      stock?: number;
      maxUses?: number;
      bank?: number;
      maxChildren?: number;
    } | null;
    requests?: Array<{
      request?: string;
      parentId?: string;
      childId?: string;
      bank?: number;
      observed?: number;
      reserved?: number;
      status?: string;
      decision?: string | null;
      watch?: string[];
      crossing?: { unit?: string; tick?: number; runEnd?: number; phase?: string;
                   verdict?: number | null; minimumMp?: number; minimumMpPercent?: number } | null;
      policy?: { holyHerbStock?: number; holyHerbMaxUses?: number;
                 holyHerbTriggerUnits?: string[] } | null;
      parent?: MpRecoverySide | null;
      child?: MpRecoverySide | null;
    }>;
  } | null;
  /**
   * The library the host currently has open, as a full path. The toolbar shows the basename so the
   * control names the file rather than the folder it lives in.
   */
  library?: string | null;
  /**
   * The extended banks. `first` builds hold **first place at something** - first in any lane, or the
   * fight's highest potential or highest earned - and are given `bank` runs instead of the ordinary
   * `ordinary`. `topEarned` builds are each fight's top ten by best earned and are given
   * `topEarnedBank`. Both keep receiving runs while they qualify; the moment a build drops out of both
   * groups it falls back to the ordinary policy, which it has already outgrown, so it is handed nothing
   * further. The build is not removed - it is out of the top positions, not the library.
   */
  rankOne?: { candidates: number; first: number; topEarned: number; rankReview: number;
              bank: number; topEarnedBank: number; rankReviewBank: number;
              ordinary: number } | null;
  /**
   * The search-space contract this library's candidates were generated under, published by the
   * host from the library itself. `SEARCH_SPACE_VERSION` is the generator's own version, so a
   * smaller number here means the library predates the current contract.
   */
  searchSpaceVersion?: number | null;
  totalRuns: number;
  /**
   * The objective generation this library runs under. A library written before the lane objective
   * reports 1 and is offered an explicit upgrade; it is never reinterpreted silently.
   */
  objectiveVersion?: number | null;
  objectiveMigration?: Record<string, unknown> | null;
  /**
   * Every elite lane's leader per `"<encounterId>:<defeatCount>"`: four independent ways a build can
   * still be promising, none of which is a single weighted score. A lane leader is a *discovery
   * parent*; whether it is a *reliable* strategy is the separate `validated`/win reading shown
   * beside it.
   */
  eliteLanes?: Record<string, Partial<Record<LaneName, LaneLeader | null>>> | null;
  /** How much of the population could enter each lane (the setup lane is empty for old runs). */
  laneEvidence?: { population?: number; withProgress?: number;
                   members?: Partial<Record<LaneName, number>> } | null;
  /** The simulation safety limit in force for this library, in combat ticks. */
  horizonTicks?: number | null;
  /** The five encounter cards and their four difficulties, grouped from the recovered catalogue. */
  campaigns?: EncounterCampaign[] | null;
  /**
   * The encounter ids this library actually holds a build for. Read from the library itself, so the
   * expansion action is offered only when fights are genuinely missing - a complete library is never
   * invited to re-add its own baselines.
   */
  libraryEncounters?: number[] | null;
  /**
   * The encounter every *new* attempt is focused on right now, or null for the normal distribution.
   * The host owns this value, so the overview shows the live focus rather than a second copy that
   * could drift from the scheduler.
   */
  focusEncounter?: number | null;
  focusEncounters?: number[];
  campaign?: { status?: string; completed?: number; total?: number; reason?: string | null } | null;
  /**
   * The host's four-objective search portfolio for the focused fight, published per scheduler pass.
   * It exists only while a fight is focused, and it describes that fight's own intensive allocation:
   * four objective lanes (each a breadth bound of builds that clear its evidence rule), a bounded
   * active set, a reserved challenger, and a reversible graduation/maintenance tier. `None`/absent
   * means the host has published nothing, so the Ranked strategies card stays exactly as it was.
   */
  focusPortfolio?: FocusPortfolio | null;
  /**
   * Why a Running search has free workers and nothing to dispatch, as an actionable sentence, or
   * null while it is genuinely working. A focused fight that cannot be branched any further is
   * stalled by construction, so the toolbar must say so rather than present it as active search.
   */
  idleReason?: string | null;
  /** Per `"<encounterId>:<defeatCount>"` readings for the cards: attempts and the two chest maxima. */
  encounterStats?: Record<string, EncounterStat> | null;
  encounterAverageLeaders?: OverviewAverageLeadersResult | null;
  /** Encounter-wide lifetime record holders, keyed `"<metric>:<encounterId>:<defeatCount>"`. */
  recordHolders?: Record<string, { value?: number; verdict?: number; candidate?: string;
                                   mathSeed?: number; libSeed?: number; digest?: string;
                                   tickLimit?: number }> | null;
  /** What the host proved while reconstructing lifetimes for an older library, if it had to. */
  encounterLifetime?: { reconstructed?: boolean; shortfall?: number; maximaComplete?: boolean;
                        singleEncounter?: boolean } | null;
  /** Runs recorded since the current Start session began; null until a session has started. */
  sessionRuns?: number | null;
  sessionElapsedSeconds?: number;
  averageSimulationSeconds?: number | null;
  currentRunElapsedSeconds?: number | null;
  timedRuns?: number;
  proposals: number;
  improvements: number;
  lastImprovementRun: number | null;
  diskBytes: number;
  provenance: Provenance;
  acceleration?: Acceleration | null;
  encounters?: Record<string, EncounterInfo> | null;
  /** The settings the running pool was started with, so a rate can say what produced it. */
  throughput?: {
    workers?: number;
    effectiveBattleWorkers?: number;
    capacityWorkers?: number;
    duty?: number;
    ceiling?: number;
    accelerators?: string[];
    secondsPerRun?: number | null;
    battlesPerSecond?: number | null;
    battlesPerHour?: number | null;
    windowSeconds?: number | null;
    capacityBattlesPerSecond?: number | null;
    achievedFraction?: number | null;
    rateBasis?: string | null;
    secondsPerRunBasis?: string | null;
    secondsPerRunWindowSeconds?: number | null;
    cpuUtilization?: number | null;
    cpuUtilizationBasis?: string | null;
  } | null;
  /**
   * The coordinator's own scheduler readings.
   *
   * `activeWorkers` counts tasks whose lifetime is open (submitted but not yet harvested) and
   * `activeWorkersAverage10s`/`workerOccupancy` are the time-weighted average of that count and its
   * share of the configured pool - the direct measurement, as opposed to derived battles/second
   * ratios. The idle split says how much idle worker time happened while ready work existed
   * (`idleCoordinatorSeconds`) versus with nothing authorised to hand out (`idleNoWorkSeconds`).
   */
  scheduler?: {
    workers?: number;
    busy?: number;
    activeWorkers?: number;
    activeWorkersAverage10s?: number | null;
    workerOccupancy?: number | null;
    idleWorkerSeconds?: number;
    idleCoordinatorSeconds?: number;
    idleNoWorkSeconds?: number;
    coordinatorBusySeconds?: number;
    communityProposalSeconds?: number | null;
    communityLastPassSeconds?: number | null;
    communityPreparation?: { workers?: number; reason?: string; specs?: number } | null;
    communityProposalWorkers?: { poolAlive?: boolean; poolWorkers?: number; lastError?: string | null } | null;
    executionQueue?: {
      workers?: number;
      inflight?: number;
      readyQueueDepth?: number;
      executingWorkers?: number;
      coolingWorkers?: number;
      availableWorkers?: number;
      completedUnharvested?: number;
      windowCapacity?: number;
      admissionCapacity?: number;
      readyWindow?: number;
    } | null;
    nativeProcess?: {
      active?: boolean;
      processRunning?: boolean;
      stage?: string;
      pid?: number | null;
      acceptedCommands?: number;
      durableJournalResults?: number;
      importedJournalResults?: number;
      pendingImportResults?: number;
      configuredExecutors?: number;
      executorActivity?: string;
      workCounters?: {
        nativeInFlight?: number;
        readyQueueDepth?: number;
        executingWorkers?: number;
        completedUnharvested?: number;
        nativeCompleted?: number;
        nativeWorkerBusySeconds?: number;
        meanWorkerJobSeconds?: number;
        nativeCallSeconds?: number;
        dispatchPending?: number;
        acceptedWork?: number;
        nativeSuccessfulBattles?: number;
        durablyCompletedThisProcess?: number;
        pendingDurableResults?: number;
        unsavedSuccessfulResults?: number;
        harvestedAwaitingSave?: number;
        acceptedOutstanding?: number;
        acceptedOutstandingTrials?: number;
        uncommittedCompletedTrials?: number;
        remainingUnsubmittedTrialSlots?: number;
        durableTrials?: number;
        generationTrialsDone?: number;
        generationTrialsTotal?: number;
        executors?: number;
        workTelemetry?: {
          accepted?: number;
          queued?: number;
          active?: number;
          pendingSave?: number;
          durable?: number;
          perEncounter?: Record<string, unknown>;
        };
      };
    } | null;
  } | null;
  candidates: Candidate[];
  archive: ArchiveRow[];
  /** Every threshold/range analysis the host has published, across all strategies. */
  probes?: Probe[] | null;
  /** What the most recent `probe` command created, reused and refused. */
  lastProbe?: LastProbe | null;
  /**
   * The host's fine-tuning program views, one per frozen-parent program. A program is a controlled
   * experiment, not a population member, so it is published here rather than in `candidates`.
   */
  fineTune?: FineTuneProgram[] | null;
  /** The most recently created fine-tuning program, or null when none was created this session. */
  lastFineTune?: FineTuneProgram | null;
  /**
   * The automatic-tuning diagnostic for the focused fight, or null when the host has not published
   * one. `autoTuneReason` is the same object's one-line explanation, published separately.
   */
  autoTune?: AutoTune | null;
  autoTuneReason?: string | null;
  /**
   * The encounter-aware search snapshot, when the host build publishes one. Optional so an older host
   * simply omits it; the new panel then shows "Backend update required" and the legacy controls stay
   * fully usable. Never mutated here - the page only renders it.
   */
  encounterAware?: EncounterAwareSnapshot | null;
};

/** One side of an MP-recovery comparison: the parent (item-free) or the child (herb-enabled). */
type MpRecoverySide = {
  candidate?: string;
  attempts?: number;
  wins?: number;
  losses?: number;
  unresolved?: number;
  earnedSamples?: number;
  meanEarned?: number | null;
  eaMeanEarned?: number | null;
  eaBestEarned?: number | null;
  eaEarnedSamples?: number;
  bestEarned?: number | null;
  herbRuns?: number;
  herbUses?: number;
  herbUseRuns?: number;
  mp?: Record<string, { minimumMp?: number; minimumMpPercent?: number; lowRuns?: number;
                        zeroRuns?: number; runs?: number; firstLowMpTick?: number }>;
};

type CommandResult = {
  ok?: boolean;
  error?: string;
  /** The library the host now has open, when the action opened or switched one. */
  library?: string;
  /** True when a library action found the file already on disk and opened it instead of creating it. */
  existed?: boolean;
  /** True when the library named was already the open one. */
  unchanged?: boolean;
  /** The host's own sentence describing what a library action did. */
  notice?: string;
};
type ReplayResult = {
  formation?: StrategyFormation;
  ok?: boolean;
  replay?: unknown;
  scenario?: unknown;
  error?: string;
  /**
   * The recovered visual identity for this run's own party, joined by the host from the canonical
   * job table. Without it the renderer has no job identity for the research fixture's fighters and
   * draws labelled placeholders; with it they are drawn by the shared loadout renderer.
   */
  visualSetup?: ReplayVisualSetup | null;
  jobIdentity?: { derived?: string[]; unknown?: string[] } | null;
  warnings?: string[];
};

/**
 * A started on-demand diagnostic export. The host returns its job id at once and reports progress
 * separately, because the archive is written by a background reader rather than blocking the reply.
 */
type DiagnosticExportResult = CommandResult & {
  jobId?: string;
  path?: string;
  count?: number;
  cancelled?: boolean;
};
/** One cheap cached reading of the running diagnostic export, for the progress line beside the button. */
type DiagnosticExportStatus = {
  ok?: boolean;
  error?: string;
  jobId?: string;
  state?: "running" | "done" | "error";
  stage?: string;
  stageLabel?: string;
  elapsedSeconds?: number;
  requested?: number;
  included?: number;
  truncated?: number;
  examined?: number;
  bytesWritten?: number;
  capBytes?: number | null;
  ratePerSecond?: number | null;
  etaSeconds?: number | null;
  path?: string;
  message?: string | null;
};

/**
 * One scored strategy row for a single encounter, as the host aggregates it over that fight's runs.
 * Every metric is read as data: a missing mean is genuinely unknown (`null`) and is never filled in
 * from a maximum, and `meanPotential` is a defeats-only opportunity mean, not a yield.
 */
type EncounterStrategyRow = {
  windows?: EncounterEvidenceWindow[];
  candidateId: string;
  /**
   * The structured name for *this* candidate: `<creator> · <unit and actual old->new change> ·
   * from <immediate parent's producer>`, derived host-side from lineage and the two scenarios. Absent
   * on a root build (which has no change to describe) or on an older host build, in which case the
   * recorded `label` is shown instead.
   */
  displayName?: string | null;
  creator?: string | null;
  change?: string | null;
  parentId?: string | null;
  parentProducer?: string | null;
  nameDerived?: boolean | null;
  nameDetail?: string | null;
  label: string | null;
  source: string;
  attempts: number;
  wins: number;
  losses: number;
  noVerdict: number;
  meanEarned: number | null;
  eaMeanEarned?: number | null;
  eaBestEarned?: number | null;
  eaEarnedSamples?: number;
  earnedSamples: number;
  meanPotential: number | null;
  potentialSamples: number;
  bestEarned: number | null;
  bestPotential: number | null;
  winRate: number | null;
  comparable: boolean;
};

/** A record holder whose candidate was pruned; its frozen scenario keeps it replayable. */
type OrphanHolder = { key: string; value: number; candidateId?: string; label?: string };

type EncounterStrategiesResult = {
  ok?: boolean;
  error?: string;
  strategies?: EncounterStrategyRow[] | null;
  orphanHolders?: OrphanHolder[] | null;
};

/** The four click-through readings in the Encounter overview, as the metric cell names them. */
type OverviewMetricName = "highest-potential" | "highest-earned" | "avg-potential" | "avg-earned";

/**
 * The overview reading a reader clicked, before the ranked list has resolved which row owns it.
 * `candidateId` is the exact owner when the host published one; `holderKey` names the frozen record
 * holder for a lifetime maximum, so a record whose candidate was pruned is still addressable. The
 * `provenance` line travels with the focus so the highlighted row can say which reading chose it.
 */
type OverviewMetricFocus = {
  /** Increments on every click, so re-clicking the same reading still re-scrolls to its row. */
  nonce: number;
  metric: OverviewMetricName;
  encounterId: number;
  candidateId: string | null;
  holderKey: string | null;
  provenance: string;
};

/** Which ranked row (or orphan holder) an overview metric resolved to, and why if it found none. */
type OverviewMetricOwner = {
  pending: boolean;
  row: EncounterStrategyRow | null;
  holder: OrphanHolder | null;
  missing: string | null;
};

/**
 * One encounter/difficulty's best MEASURED MEAN build for a metric, exactly as the host ranks it
 * (`_mean_leaders`). `mean` uses the recorded aggregate evidence - a defeat contributes the native
 * loss gate's zero, for potential it is a defeat's own reached opportunity - `samples` is how many
 * measured outcomes produced it, and `attempts` is the build's lifetime attempt count, so a mean taken
 * over fewer rows than attempts stays visible instead of looking like a lifetime fact. `comparable`
 * is false when measured earned outcomes do not cover every attempt or any result is unresolved.
 */
type AverageLeader = {
  candidateId: string;
  label: string;
  source: string;
  mean: number;
  samples: number;
  attempts: number;
  comparable: boolean;
};

/** One fight's two average leaders, as `overview_average_leaders` returns them (either may be null). */
type OverviewAverageLeaderRow = {
  encounterId: number;
  defeatCount: number;
  residentCount: number;
  highestAvgEarned?: AverageLeader | null;
  highestAvgPotential?: AverageLeader | null;
};

/**
 * The host's whole-overview average read: one entry per encounter/difficulty that still holds a
 * resident build, each carrying its best mean earned and best mean potential builds. A fight with no
 * measured mean for a metric reports null there rather than falling back to a maximum, so a missing
 * average is honest and not a zero.
 */
type OverviewAverageLeadersResult = {
  ok?: boolean;
  error?: string;
  minSamples?: number;
  leaders?: OverviewAverageLeaderRow[] | null;
};

/**
 * The four objectives the focused fight's intensive capacity is shared between, named exactly as the
 * host's portfolio module names them. Kept as a union so the lane map cannot be read with a stray key.
 */
type PortfolioObjective =
  | "highest-earned"
  | "highest-potential"
  | "average-earned"
  | "average-potential";

/** The two views of the portfolio that are not an objective lane: the graduated tier. */
type PortfolioView = PortfolioObjective | "stable";

/**
 * One build a portfolio lane studies, exactly as `focused_portfolio_lanes` publishes it. `value` is
 * that objective's own number (a maximum is only ever compared with a maximum, a mean with a mean),
 * `samples` is how many observations it stands on, and `comparable` says whether the objective's
 * workload is the shared validation bank. A lane holds only builds that clear its evidence rule.
 */
type PortfolioLaneMember = {
  objective?: string | null;
  candidate: string;
  label?: string | null;
  source?: string | null;
  value?: number | null;
  samples?: number | null;
  winLower?: number | null;
  comparable?: boolean;
};

/** One bounded allocation decision: an active slot, the reserved challenger, or a maintenance recheck. */
type PortfolioDecision = {
  candidate: string;
  objective?: string | null;
  tier?: string | null;
  value?: number | null;
  samples?: number | null;
  reason?: string | null;
  current?: number | null;
  target?: number | null;
  granted?: number | null;
  limit?: number | null;
  outcome?: string | null;
};

/**
 * The host's focused four-objective portfolio for one encounter, as `focused_portfolio` returns its
 * plan. It is pure scheduling data: `stable`/`graduated` are candidate ids, `gaps` is how many of a
 * lane's ten slots stand empty, and nothing here claims a build is globally optimal.
 */
type FocusPortfolio = {
  passNumber?: number | null;
  lanes?: Partial<Record<PortfolioObjective, PortfolioLaneMember[] | null>> | null;
  laneSizes?: Partial<Record<PortfolioObjective, number>> | null;
  gaps?: Partial<Record<PortfolioObjective, number>> | null;
  overlap?: Record<string, string[]> | null;
  active?: PortfolioDecision[] | null;
  challenger?: PortfolioDecision[] | null;
  maintenance?: PortfolioDecision[] | null;
  graduated?: string[] | null;
  stable?: string[] | null;
  budget?: number | null;
  granted?: number | null;
  maintenanceBudget?: number | null;
  maintenanceGranted?: number | null;
  minMeanEvidence?: number | null;
  laneSize?: number | null;
  activeSlots?: number | null;
  challengerSlots?: number | null;
  stableMinRuns?: number | null;
  stablePlateau?: number | null;
  recheckEvery?: number | null;
};

/**
 * One run persisted in the investigation sidecar. It is the runner's own compact metric dict (the
 * same shape the stored examples use) plus the two per-run scored chest readings the host adds for
 * the investigation sidecar when it has them.
 */
type StrategyRun = Example & {
  /** The host's own bank/position for this row, used only to keep each row's key stable. */
  phase?: string | null;
  ordinal?: number | null;
  /** Chests this exact run earned (victory) or reached as an opportunity (defeat). */
  earned?: number | null;
  potential?: number | null;
  /** The host's own label for how that number was read; never re-derived on the page. */
  basis?: string | null;
};

type EncounterEvidenceWindow = {
  kind: string;
  experiment: number;
  measurementWindow: string;
  policy: unknown;
  engine: string | null;
  mechanics: string | null;
  encounterRevision: string | null;
  attempts: number;
  wins: number;
  losses: number;
  noVerdict: number;
  errors: number;
  censored: number;
  unknownOutcome: number;
  earnedCount: number;
  meanEarned: number | null;
  potentialCount: number;
  meanPotential: number | null;
};

export function EncounterEvidenceReadings({ windows }: { windows: EncounterEvidenceWindow[] }) {
  if (!windows.length) return null;
  return <div className="space-y-2" data-community-readings>
    <p className="text-sm font-semibold">Saved Community measurements</p>
    <p className="text-xs text-muted-foreground">Development and confirmation are shown separately for each tested set of conditions. These measurements remain available when a build has been pruned.</p>
    <div className="overflow-x-auto"><Table>
      <TableHeader><TableRow><TableHead>Measurement</TableHead><TableHead>Attempts</TableHead><TableHead>Wins / losses / no verdict</TableHead><TableHead>Mean earned</TableHead><TableHead>Mean potential · losses</TableHead><TableHead>Uncertain results</TableHead></TableRow></TableHeader>
      <TableBody>{windows.map((row, index) => <TableRow key={`${row.kind}-${row.experiment}-${index}`}>
        <TableCell><div>{row.kind === "confirmation" ? "Confirmation" : "Development"}{row.experiment != null ? ` · study ${row.experiment}` : " · saved samples"}</div>
          <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Tested conditions</summary><div className="max-w-sm break-words whitespace-normal">Window: {row.measurementWindow}<br />Policy: {JSON.stringify(row.policy)}<br />Engine: {row.engine ?? "unknown"}<br />Mechanics: {row.mechanics ?? "unknown"}<br />Encounter revision: {row.encounterRevision ?? "unknown"}</div></details>
        </TableCell>
        <TableCell>{formatInt(row.attempts)}</TableCell>
        <TableCell>{formatInt(row.wins)} / {formatInt(row.losses)} / {formatInt(row.noVerdict)}</TableCell>
        <TableCell>{formatMetric(row.meanEarned)} <span className="text-xs text-muted-foreground">n={formatInt(row.earnedCount)}</span></TableCell>
        <TableCell>{formatMetric(row.meanPotential)} <span className="text-xs text-muted-foreground">n={formatInt(row.potentialCount)}</span></TableCell>
        <TableCell className="text-xs">{formatInt(row.errors)} errors · {formatInt(row.censored)} censored · {formatInt(row.unknownOutcome)} unknown</TableCell>
      </TableRow>)}</TableBody>
    </Table></div>
  </div>;
}

type StrategyDetail = {
  encounterLedger?: { windows: EncounterEvidenceWindow[] } | null;
  outcomeDistribution?: OutcomeDistribution;
  formation?: StrategyFormation;
  ok?: boolean;
  error?: string;
  candidateId?: string | null;
  label?: string;
  scenario?: unknown;
  holder?: unknown;
  /** Whether the target's candidate row is still resident in the library (false once pruned). */
  resident?: boolean;
  storedRuns?: StrategyRun[] | null;
  trialRuns?: StrategyRun[] | null;
  storedSummary?: BankSummary | null;
  trialSummary?: BankSummary | null;
  /**
   * Present only on a `simulate_strategy_visual` reply: the one fresh fight's replay-compatible
   * trace and visual identity, plus the fresh seed pair and digest it was recorded under. A plain
   * `strategy_detail` never carries these, so the page can tell a new simulation from a stored one.
   */
  replay?: unknown;
  visualSetup?: ReplayVisualSetup | null;
  jobIdentity?: { derived?: string[]; unknown?: string[] } | null;
  warnings?: string[];
  digest?: string;
  mathSeed?: number;
  libSeed?: number;
  seeds?: number[] | null;
  /**
   * Only on a `run_strategy` reply: the host's own summary of that one chunk call's rows
   * (`_summarize_attempts`). The page merges one of these per chunk so a multi-chunk batch is
   * aggregated as a batch instead of being read off the cumulative trial total.
   */
  batchSummary?: HostBankSummary | null;
};

/** Which build the investigation panel describes: a live candidate, or a frozen record holder. */
type InvestigationTarget = { candidateId?: string | null; holderKey?: string | null };

/** The frozen record-holder fields the host returns with a holder-scoped `strategy_detail`. */
type HolderInfo = {
  key?: string;
  value?: number;
  verdict?: number;
  candidate?: string | null;
  label?: string;
  mathSeed?: number;
  libSeed?: number;
  digest?: string;
  tickLimit?: number;
};

/**
 * One bank's summary as the investigation host publishes it (`_summarize_attempts`). It is flat and
 * belongs to this workflow only: `attempts` is the build's lifetime attempt total, `samples` is how
 * many replay rows are still retained; outcome counts and means use all recorded aggregate evidence. A
 * null mean is genuinely unmeasured and is never filled in; the page renders these fields directly.
 */
type BankSummary = {
  attempts: number;
  samples: number;
  wins: number;
  losses: number;
  noVerdict: number;
  meanEarned: number | null;
  earnedSamples: number;
  meanPotential: number | null;
  potentialSamples: number;
  bestEarned?: number | null;
  bestPotential?: number | null;
  winRate: number | null;
  comparable: boolean;
};

type StrategySortMode = "earned" | "bestEarned" | "winRate" | "potential";

type OptimizerApi = {
  restore_strategy?: (holderKey: string) => Promise<{ok: boolean; candidateId?: string; error?: string}>;
  status: () => Promise<OptimizerStatus>;
  /** Read-only, same-origin cached status route; absent only on older preview hosts. */
  status_transport?: () => Promise<{path: string}>;
  command: (action: string, value: Record<string, unknown>) => Promise<CommandResult>;
  replay: (candidateId: string, seeds: SeedPair) => Promise<ReplayResult>;
  /** Replay a persisted encounter-wide record holder from its frozen scenario. */
  replay_holder: (key: string) => Promise<ReplayResult>;
  import_build: () => Promise<CommandResult>;
  export_build: (candidateId: string) => Promise<CommandResult>;
  new_library: () => Promise<CommandResult>;
  /**
   * Open an existing library file. A separate action from `new_library` on purpose: that one uses the
   * save dialog (which the operating system pairs with a "replace?" prompt) because it names a library
   * that does not exist yet, while this one is for a library that does and never asks about replacing
   * anything. Optional so an older host build without it simply does not show the control.
   */
  open_library?: () => Promise<CommandResult>;
  /**
   * Start an on-demand diagnostic export of the most recent battles. The host opens its own save
   * dialog, then writes the archive on a background thread and returns its job id at once. It is
   * read-only - it runs no battle and changes nothing - and is allowed while a search is running.
   */
  export_diagnostics?: (count?: number) => Promise<DiagnosticExportResult>;
  /** Cached progress for one export job. Cheap; polled only while a job is actually running. */
  export_diagnostics_status?: (jobId: string) => Promise<DiagnosticExportStatus>;
  /**
   * The host's ranked strategies for one encounter/difficulty. Optional because the host builds that
   * carry it are landing alongside this page; when it is absent the page says so instead of guessing.
   */
  encounter_strategies?: (
    encounterId: number,
    defeatCount?: number,
  ) => Promise<EncounterStrategiesResult>;
  /**
   * Every encounter/difficulty's best measured mean (earned and potential), in one read, so the
   * Encounter overview's average columns cost a single call rather than a query per rendered row.
   * Optional because the host builds that carry it land alongside this page; when it is absent the
   * page says so instead of guessing.
   */
  /**
   * Read-only player-region read: the measured summary the host groups into per-archetype conditional
   * tradeoff regions. Optional so an older host build simply does not publish the surface. The
   * desktop overview no longer mounts the Measured build regions card; this host read and the
   * OptimizerPlayerRegions component are preserved for the future website surface, and are invoked
   * there only from an explicit button press - never on the status poll.
   */
  encounter_player_regions?: (candidateLimit?: number | null, encounterId?: number | null) => Promise<PlayerRegionsSummary | null>;
  overview_average_leaders?: () => Promise<OverviewAverageLeadersResult>;
  /** One build's stored runs, frozen scenario and summaries, by candidate id or record-holder key. */
  strategy_detail?: (
    candidateId?: string | null,
    holderKey?: string | null,
  ) => Promise<StrategyDetail>;
  start_strategy_experiment?: (candidateId: string | null, holderKey: string | null, count: number,
    mode: string) => Promise<{ok: boolean; error?: string; candidateId?: string}>;
  experiment_report?: (candidateId?: string) => Promise<{ok: boolean; error?: string; experiment?: FocusedExperiment | null; batches?: FocusedExperiment[]}>;
  /** Run 1..8 fresh seed trials and return the same payload with the sidecar updated. */
  run_strategy?: (
    candidateId?: string | null,
    holderKey?: string | null,
    count?: number,
  ) => Promise<StrategyDetail>;
  /**
   * Run one brand-new fight of the target's exact frozen build on a fresh seed pair the target has
   * never recorded, with `trace=True`, and return the updated detail together with that one fight's
   * replay-compatible trace, visual identity, fresh seeds and digest. Distinct from a same-seed
   * replay: this is a new simulation, not a reproduction of a stored run.
   */
  simulate_strategy_visual?: (
    candidateId?: string | null,
    holderKey?: string | null,
  ) => Promise<StrategyDetail>;
  /**
   * Replay one recorded investigation run - stored, fresh trial or frozen holder - by its own seed
   * pair. `replay` only accepts a seed pair the candidate's own example bank holds, so this is the
   * control the investigation run tables use; the host verifies the rerun against the recorded digest.
   */
  replay_strategy_run?: (
    candidateId?: string | null,
    holderKey?: string | null,
    seeds?: SeedPair | null,
  ) => Promise<ReplayResult>;
  /**
   * Read-only host API: the fine-tuning programs for one parent build (or every program when no
   * parent is named). It is the inspect entry point for the investigation panel, so it never mutates
   * the library and needs no pause; the same views are also published in `status.fineTune`.
   */
  fine_tune_programs?: (candidateId?: string | null) => Promise<FineTuneProgram[]>;
};

declare global {
  interface Window {
    pywebview?: { api?: OptimizerApi };
  }
}

function hostApi(): OptimizerApi | null {
  if (typeof window === "undefined") return null;
  return window.pywebview?.api ?? null;
}

/* ------------------------------------------------------------------ */
/* Constants and formatting                                            */
/* ------------------------------------------------------------------ */

const POLL_INTERVAL_MS = 2000;
/**
 * How long the last host reply stays authoritative. The focused-search idle warning is only ever
 * shown from a snapshot the host actually sent: replaying a "nothing to dispatch" sentence after the
 * host has stopped answering would claim a stall the page can no longer prove, so the warning clears
 * itself once no reply has landed in this long. The host publishes at least every couple of seconds
 * while it is running, so a live stall is refreshed well inside this window.
 */
const IDLE_WARNING_STALE_MS = 10_000;
/**
 * The Encounter overview's average columns are the one read that costs the host a real aggregate
 * query, so they are refreshed on their own bounded cadence rather than on every `status` poll:
 * while a search is running, this often enough that a mean that just improved becomes visible, and
 * once more when it stops or the library changes. The lifetime maxima ride along on `status` and stay
 * current without any extra call.
 */
const AVERAGE_REFRESH_INTERVAL_MS = 30_000;
const DISCOVERY_RUNS = 8;
const SELECTION_RUNS = 64;
/**
 * Worker/duty defaults mirror `strategy_optimizer_limits.py`. The sweep behind them (PyPy 3.11.15,
 * full-horizon encounter, through the worker pipe) measured 0.65 / 1.28 / 2.19 / 3.02 / 4.52 / 4.22 /
 * 4.43 / 4.69 battles per second at 1 / 2 / 4 / 8 / 12 / 16 / 20 / 24 workers: the curve is flat
 * past ~12, so 12 stays the default. 16 and 24 are offered because the native backend has been
 * measured there, but the list is filtered by the host's own `worker_ceiling()` (which keeps four
 * logical CPUs free and never exceeds `MAX_WORKERS`), so a smaller machine is never offered a count
 * it cannot run. The host clamps on Start as well: these are preferences, not promises.
 */
const WORKER_CHOICES = [1, 2, 4, 8, 12, 16, 24] as const;
const DEFAULT_WORKERS = 12;
const DEFAULT_DUTY = 0.9;
/**
 * On-demand diagnostic export bounds, mirroring `strategy_diagnostic_export`. The user picks how many
 * of the most recent battles to include; the host enforces the 500 MB cap and the file itself is never
 * allowed to exceed it.
 */
const DIAGNOSTIC_EXPORT_DEFAULT = 1000;
const DIAGNOSTIC_EXPORT_MAX = 1_000_000;
const DIAGNOSTIC_EXPORT_CAP_LABEL = "500 MB";
/**
 * The generator's search-space contract, mirrored from `search_contract.SEARCH_SPACE_VERSION`. The
 * host publishes each library's own `searchSpaceVersion`; the toolbar names a library older than
 * this value instead of silently searching it under assumptions it was not written for.
 */
const SEARCH_SPACE_VERSION = 3;
/** Rolling window for the observed battles/second, and the shortest window worth reporting. */
const RATE_WINDOW_MS = 20000;
const RATE_MIN_SECONDS = 5;
const RECOMMENDED_WIN_LOW = 0.8;
const RECOMMENDED_CALLBACK_SHARE = 0.95;
const REPLAY_WARNINGS = ["strategy-optimizer:selected-replay"];
const NO_FAMILY = "Unclassified (not yet validated)";
/** How many investigation run rows one table shows before it offers "Show more". */
const INVESTIGATION_RUN_PAGE = 25;
/**
 * How often the investigation panel re-reads a parent's fine-tuning programs while the search is
 * running. The programs advance on the optimiser's own slow cadence, so a bounded 5 s read follows
 * them without issuing a query on every 2 s status tick; the panel also re-reads once after the
 * search pauses, so a stopped program is shown at its final measured point.
 */
const FINE_TUNE_REFRESH_MS = 5000;

function formatInt(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(Math.trunc(value)) : "—";
}

/**
 * Parse the `Simulate # fights` box. An empty or non-numeric entry is not a number, so the caller
 * can leave the field alone while it is being edited and only clamp on blur or submit.
 */
function parseTrialCount(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? Math.trunc(value) : null;
}

/** Clamp an entered total into the count box's own range; the host chunks the work by eight. */
function clampTrialCount(value: number): number {
  return Math.min(FRESH_TRIAL_MAX, Math.max(FRESH_TRIAL_MIN, Math.trunc(value)));
}

/**
 * `HH:MM:SS` for the host's active-session reading. The host owns the value; this only formats it,
 * so a re-render or a poll can never change what the elapsed time is.
 */
function formatElapsed(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "—";
  const total = Math.floor(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

/**
 * `M:SS` nominal in-game battle time for a tick count. The original game advances the battle 20
 * times per second (recovered target frame rate), so a tick is 50 ms of fought time.
 */
function formatTicksClock(ticks: number | null | undefined): string {
  if (typeof ticks !== "number" || !Number.isFinite(ticks) || ticks <= 0) return "—";
  const seconds = Math.round(ticks / 20);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** Thousands-separated count, for limits and totals that are read as numbers (10,000 ticks). */
function formatCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.trunc(value).toLocaleString("en-US")
    : "—";
}

function formatNumber(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function formatInterval(interval: [number, number] | undefined): string {
  if (!Array.isArray(interval) || interval.length < 2) return "—";
  return `${formatNumber(interval[0], 2)}–${formatNumber(interval[1], 2)}`;
}

function formatBytes(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

function shortId(id: string): string {
  return id.length > 10 ? id.slice(0, 10) : id;
}

function stateCategory(state: string): KaCategory {
  const value = state.toLowerCase();
  if (value.includes("error")) return "warning";
  if (value.includes("running")) return "success";
  if (value.includes("saving") || value.includes("opening")) return "warning";
  return "muted";
}

function isCompleteSelection(candidate: Candidate): boolean {
  return Boolean(candidate.selection) && candidate.selection.n >= SELECTION_RUNS && candidate.selection.comparable;
}

function parseQuality(raw: string): [number | null, number | null, number | null] | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length < 3) return null;
    const pick = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
    return [pick(parsed[0]), pick(parsed[1]), pick(parsed[2])];
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Result-first readings                                               */
/* ------------------------------------------------------------------ */

/**
 * The single place retained chests are read. The runner reports `retainedMean` as null while
 * automatic Finish and reward collection are unmodelled, so the reading stays explicitly unknown:
 * it is never rendered as zero and never substituted with a prize-callback count. A future runner
 * that supplies a real number is shown unchanged, and only then may retained ranking be offered.
 */
type RetainedReading = { known: true; mean: number; samples: number | null } | { known: false };

function retainedReading(summary: Summary | null | undefined): RetainedReading {
  const mean = summary?.retainedMean;
  if (typeof mean === "number" && Number.isFinite(mean)) {
    return { known: true, mean, samples: summary?.retainedSampleCount ?? null };
  }
  return { known: false };
}

/** Retained chests recorded on one exact run, or null when the runner exposes nothing. */
function retainedRunValue(example: Example | undefined): number | null {
  const value = example?.retained;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatRetainedPerRun(reading: RetainedReading): string {
  return reading.known ? `${formatNumber(reading.mean, 2)} / run` : "not countable yet";
}

function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "—";
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}

type RunOutcome = "win" | "loss" | "unresolved" | "unknown";

/** Outcome read strictly from the run's own verdict/censored fields. No award is inferred. */
function runOutcome(example: Example | undefined): RunOutcome {
  if (!example) return "unknown";
  if (example.censored) return "unresolved";
  if (example.verdict === 1) return "win";
  if (example.verdict === 2) return "loss";
  return "unknown";
}

const RUN_OUTCOME_LABEL: Record<RunOutcome, string> = {
  win: "Win",
  loss: "Loss",
  unresolved: "No verdict by limit",
  unknown: "Outcome not recorded",
};

/** The family label without its redundant "Encounter N · " prefix, which is shown separately. */
function familyDetail(family: string | null): string {
  return (family ?? NO_FAMILY).replace(/^Encounter \d+ · /, "");
}

/** The encounter a strategy's scenario belongs to, or null when the scenario carries no id. */
function encounterIdOf(candidate: Candidate | null | undefined): number | null {
  const value = (candidate?.scenario as { encounterId?: unknown } | null | undefined)?.encounterId;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

type PartyMember = {
  name?: string | null;
  human?: boolean | null;
  monsterId?: number | null;
  visitor?: boolean | null;
};

/**
 * Who the strategy brings. Read from the scenario the run actually used: human units are allies,
 * anything non-human is a pet/monster companion. The baseline party is all humans, which is why a
 * pet list can legitimately be empty rather than missing.
 */
function partyOf(candidate: Candidate | null | undefined): { allies: string[]; pets: string[] } {
  const units = (candidate?.scenario as { ownUnits?: PartyMember[] } | null | undefined)?.ownUnits ?? [];
  const allies: string[] = [];
  const pets: string[] = [];
  for (const unit of units) {
    const name = unit?.name ?? (unit?.monsterId != null ? `Monster ${unit.monsterId}` : "unnamed unit");
    (unit?.human === false ? pets : allies).push(name);
  }
  return { allies, pets };
}

type ChestReading =
  | { known: true; count: number; basis: string }
  | { known: false; reason: string };

/**
 * Chests one stored run released. The runner sends `chests`/`chestBasis`; rows written before that
 * field existed fall back to the same rule here (0 on a loss, the recorded count at victory), so an
 * older library still shows a real number instead of a blank.
 */
function chestReading(example: Example | undefined): ChestReading {
  const value = example?.chests;
  if (typeof value === "number" && Number.isFinite(value)) {
    return { known: true, count: value, basis: example?.chestBasis ?? "recorded" };
  }
  const outcome = runOutcome(example);
  if (outcome === "loss") return { known: true, count: 0, basis: "loss-gate" };
  const prizes = example?.prizeCallbacks;
  if (outcome === "win" && typeof prizes === "number" && Number.isFinite(prizes)) {
    return { known: true, count: prizes, basis: "queued-at-victory" };
  }
  return {
    known: false,
    reason: outcome === "unresolved"
      ? "the fight never reached a verdict, so nothing is claimed"
      : "this run recorded no chest signal",
  };
}

/** Plain-language basis for a chest number, so 0 and 21 are never confused with each other. */
function chestBasisLabel(basis: string): string {
  switch (basis) {
    case "certified-award":
      return "certified award at victory";
    case "awarded":
      return "awarded at victory";
    case "queued-at-victory":
      return "queued at victory";
    case "loss-gate":
      return "no chest — the loss gate dispatches none";
    default:
      return "recorded at victory";
  }
}

/** Mean chests per resolved run for a strategy, with how many runs that mean covers. */
function chestsPerRun(
  summary: Summary | null | undefined,
): { mean: number; samples: number | null; se: number | null } | null {
  const mean = summary?.chestMean;
  if (typeof mean !== "number" || !Number.isFinite(mean)) return null;
  const se = summary?.chestSE;
  return {
    mean,
    samples: summary?.chestSampleCount ?? null,
    se: typeof se === "number" && Number.isFinite(se) ? se : null,
  };
}

type ChestScope = "selection" | "validation" | "discovery";
type ChestMean = { mean: number; samples: number | null; se: number | null; scope: ChestScope };

/**
 * Chests per run for a strategy, preferring the comparable selection bank.
 *
 * A strategy that has only been screened so far has an empty selection bank, and reporting that as
 * "no resolved run yet" while its stored runs plainly show chests would be misleading. The same
 * preference order the runner uses for its example runs applies here: selection, then the wider
 * validation aggregate, then discovery - and the scope is returned so the page can say which bank
 * the mean came from instead of quietly mixing them.
 */
function candidateChests(candidate: Candidate | null | undefined): ChestMean | null {
  const sources: Array<[ChestScope, Summary | null | undefined]> = [
    ["selection", candidate?.selection],
    ["validation", candidate?.validation],
    ["discovery", candidate?.discovery],
  ];
  for (const [scope, summary] of sources) {
    const reading = chestsPerRun(summary);
    if (reading) return { ...reading, scope };
  }
  return null;
}

type WinMean = { rate: number | null; lower: number | null; samples: number; wins: number | null; scope: ChestScope };

/** The same preference order for the win reading, so a screened strategy is not shown blank. */
function candidateWin(candidate: Candidate | null | undefined): WinMean | null {
  const sources: Array<[ChestScope, Summary | null | undefined]> = [
    ["selection", candidate?.selection],
    ["validation", candidate?.validation],
    ["discovery", candidate?.discovery],
  ];
  for (const [scope, summary] of sources) {
    if (!summary || !(summary.n > 0)) continue;
    const lower = summary.winInterval?.[0];
    return {
      rate: typeof summary.winRate === "number" && Number.isFinite(summary.winRate) ? summary.winRate : null,
      lower: typeof lower === "number" && Number.isFinite(lower) ? lower : null,
      samples: summary.n,
      wins: typeof summary.wins === "number" ? summary.wins : null,
      scope,
    };
  }
  return null;
}

const SCOPE_LABEL: Record<ChestScope, string> = {
  selection: "selection bank",
  validation: "validation runs",
  discovery: "discovery runs",
};

/** "Encounter 19 · Take out the Wairo Tank!", degrading to the bare number without a catalogue. */
function encounterLabel(id: number | null, info: EncounterInfo | undefined): string {
  if (id === null) return "Encounter not recorded";
  return info?.title ? `Encounter ${id} · ${info.title}` : `Encounter ${id}`;
}

/** One line of what that encounter actually contains, from the engine's encounter data. */
function encounterRoster(info: EncounterInfo | undefined): string | null {
  if (!info) return null;
  const parts: string[] = [];
  if (info.boss) parts.push(`boss ${info.boss}`);
  if (typeof info.enemyCount === "number") parts.push(`${formatInt(info.enemyCount)} enemies`);
  if (typeof info.level === "number") parts.push(`level ${formatInt(info.level)}`);
  return parts.length ? parts.join(" · ") : null;
}

function outcomeCategory(outcome: RunOutcome): KaCategory {
  if (outcome === "win") return "success";
  if (outcome === "loss") return "warning";
  return "muted";
}

const CERTIFICATE_BASIS = "reward-entitlement-certificate";

type AwardReading =
  | {
      known: true;
      count: number;
      certified: boolean;
      basis: string | null;
      inventoryVerified: boolean;
      pending: number | null;
      reason: string | null;
    }
  | { known: false; pending: number | null; reason: string | null };

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The per-run award, read only from the runner's `rewardOutcome`. A win without a certificate and
 * any unresolved/censored run stay unknown, and a positive count is accepted only as a certified
 * entitlement - so nothing here promotes a callback, a pending count or a certificate into a
 * retained chest. Older stored rows that predate the field can show a known 0 only for an explicit
 * uncensored loss, which is the validated native gate; every other old row stays unknown.
 */
function awardReading(example: Example | undefined): AwardReading {
  const outcome = runOutcome(example);
  if (outcome === "unresolved") return { known: false, pending: null, reason: null };
  const reward = example?.rewardOutcome ?? null;
  if (reward) {
    const awarded = finiteOrNull(reward.awardedChests);
    const pending = finiteOrNull(reward.pendingChests);
    const reason = reward.reason ?? null;
    const certified = reward.awardedBasis === CERTIFICATE_BASIS;
    if (awarded === null || (awarded > 0 && !certified)) {
      return { known: false, pending, reason };
    }
    return {
      known: true,
      count: awarded,
      certified,
      basis: reward.awardedBasis ?? null,
      inventoryVerified: reward.inventoryVerified === true,
      pending,
      reason,
    };
  }
  if (outcome === "loss") {
    return {
      known: true,
      count: 0,
      certified: false,
      basis: "native-win-loss-gate",
      inventoryVerified: false,
      pending: null,
      reason: null,
    };
  }
  return { known: false, pending: null, reason: null };
}

/** Big per-run award label; only a certified award is ever marked certified. */
function awardLabel(reading: AwardReading): string {
  if (!reading.known) return "Award unknown";
  return reading.certified ? `${formatInt(reading.count)} awarded (certified)` : `${formatInt(reading.count)} awarded`;
}

/** Supported ordering only: win-interval lower bound, then sample size. Never a retained ranking. */
function rankByWinLowerBound(candidates: Candidate[]): Candidate[] {
  const lower = (candidate: Candidate) => {
    const value = candidate.selection?.winInterval?.[0];
    return typeof value === "number" && Number.isFinite(value) ? value : -1;
  };
  return [...candidates].sort(
    (left, right) => lower(right) - lower(left) || (right.selection?.n ?? 0) - (left.selection?.n ?? 0),
  );
}

/**
 * Chests-first ordering, with reliability as the tie-break.
 *
 * A strategy with a measured mean chests-per-run sorts above one without, so a partly-searched
 * library still puts its known quantities on top. Ties break on the win lower bound, then on how
 * many resolved runs the mean actually covers - a mean over three wins is not the same claim as a
 * mean over sixty-four.
 */
function rankByChestsPerRun(candidates: Candidate[]): Candidate[] {
  const chests = (candidate: Candidate) => candidateChests(candidate)?.mean ?? -1;
  const samples = (candidate: Candidate) => candidateChests(candidate)?.samples ?? 0;
  const winLower = (candidate: Candidate) => {
    return candidateWin(candidate)?.lower ?? -1;
  };
  return [...candidates].sort((left, right) =>
    chests(right) - chests(left) ||
    winLower(right) - winLower(left) ||
    samples(right) - samples(left) ||
    (right.selection?.n ?? 0) - (left.selection?.n ?? 0));
}

/**
 * Chest-yield ordering, used only once at least one strategy reports a real `retainedMean`.
 * Strategies with a measured retained sample sort above the ones that still report none; the win
 * lower bound and sample size break ties. While retained is unknown this ordering is never chosen.
 */
function rankByChestYield(candidates: Candidate[]): Candidate[] {
  const winLower = (candidate: Candidate) => {
    const value = candidate.selection?.winInterval?.[0];
    return typeof value === "number" && Number.isFinite(value) ? value : -1;
  };
  return [...candidates].sort((left, right) => {
    const leftMean = retainedReading(left.selection);
    const rightMean = retainedReading(right.selection);
    if (leftMean.known && rightMean.known) {
      return rightMean.mean - leftMean.mean || winLower(right) - winLower(left);
    }
    if (leftMean.known) return -1;
    if (rightMean.known) return 1;
    return winLower(right) - winLower(left) || (right.selection?.n ?? 0) - (left.selection?.n ?? 0);
  });
}

/**
 * Plain-language reason this exact run is worth watching, grounded only in the runner's stored
 * outcome, survivor count and retained count. "most chests" appears only when a real retained
 * number exists; while retained is unknown the reason never implies a chest yield.
 */
/**
 * Why this run is worth a look, in the player's terms. Chest counts lead, because that is what the
 * search is for; the outcome and the survivor count break ties between runs with the same yield.
 */
function runReason(example: Example | undefined, chests: number | null, maxChests: number | null): string {
  if (chests !== null && maxChests !== null && chests >= maxChests && chests > 0) {
    return "most chests";
  }
  if (chests !== null && chests > 0) return `${formatInt(chests)} chests`;
  const outcome = runOutcome(example);
  if (outcome === "loss") return "lost — no chests";
  if (outcome === "unresolved") return "never resolved";
  if (outcome === "win") {
    if (example?.survivors === 1) return "won with 1 survivor";
    if (typeof example?.survivors === "number" && Number.isFinite(example.survivors)) {
      return `won with ${formatInt(example.survivors)} survivors`;
    }
    return "won";
  }
  return "outcome not recorded";
}

/** Clear reliability reading for one strategy, taken from its own selection bank only. */
function strategyReliability(candidate: Candidate): string {
  const samples = candidate.selection?.n ?? 0;
  if (samples === 0) return "no selection runs yet · reliability unknown";
  const runs = `${formatInt(samples)}/${SELECTION_RUNS} selection runs`;
  if (!isCompleteSelection(candidate)) {
    return samples < SELECTION_RUNS
      ? `partial sample (${runs}) — not yet reliable`
      : `partial sample (${runs}) — not comparable`;
  }
  return `well-sampled: ${runs}, comparable`;
}

/* ------------------------------------------------------------------ */
/* Joint stat region (measured, same family only)                      */
/* ------------------------------------------------------------------ */

type StatCell = { key: string; label: string; value: number | null };

/** The runner's prepared values, labelled by fighter and the runner's own numeric parameter id. */
function fighterStatCells(stats: Record<string, FighterStat> | undefined): StatCell[] {
  const cells: StatCell[] = [];
  for (const [fighter, stat] of Object.entries(stats ?? {})) {
    if (!stat) continue;
    if (typeof stat.effectiveDefense === "number") {
      cells.push({ key: `${fighter}|defense`, label: `${fighter} · defense`, value: stat.effectiveDefense });
    }
    if (typeof stat.averageTrainingLevel === "number") {
      cells.push({ key: `${fighter}|training`, label: `${fighter} · training`, value: stat.averageTrainingLevel });
    }
    for (const [pid, param] of Object.entries(stat.parameters ?? {})) {
      cells.push({ key: `${fighter}|param:${pid}`, label: `${fighter} · param ${pid}`, value: param?.value ?? null });
    }
  }
  return cells;
}

type RegionColumn = { candidate: Candidate; recommended: boolean };
type RegionRow = { key: string; label: string; values: Array<number | null> };

/**
 * The same rules the engine applies to its own archive: a complete, comparable `SELECTION_RUNS`
 * selection bank with a win-interval lower bound of at least `RECOMMENDED_WIN_LOW`, restricted to one
 * measured family. "Recommended" additionally requires a callback mean within 95% of the best
 * observed in that family. Nothing here interpolates or promises an in-game result.
 */
function regionFor(candidates: Candidate[], family: string | null): {
  columns: RegionColumn[];
  rows: RegionRow[];
  bestObserved: number | null;
} {
  const viable = candidates
    .filter(
      (candidate) =>
        Boolean(candidate.selection) &&
        candidate.selection.n >= SELECTION_RUNS &&
        candidate.selection.comparable &&
        candidate.validation.comparable &&
        candidate.validation.winInterval[0] >= RECOMMENDED_WIN_LOW &&
        Array.isArray(candidate.selection.winInterval) &&
        (candidate.selection.winInterval[0] ?? 0) >= RECOMMENDED_WIN_LOW &&
        (candidate.family ?? null) === family,
    )
    .sort((left, right) => (right.selection.callbackMean ?? -Infinity) - (left.selection.callbackMean ?? -Infinity));

  const callbackMeans = viable
    .map((candidate) => candidate.selection.callbackMean)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const bestObserved = callbackMeans.length ? Math.max(...callbackMeans) : null;
  const recommendedIds = new Set(
    bestObserved === null
      ? []
      : viable
          .filter((candidate) => (candidate.selection.callbackMean ?? -Infinity) >= bestObserved * RECOMMENDED_CALLBACK_SHARE)
          .map((candidate) => candidate.id),
  );

  const labels = new Map<string, string>();
  for (const candidate of viable) {
    for (const cell of fighterStatCells(candidate.stats)) {
      if (!labels.has(cell.key)) labels.set(cell.key, cell.label);
    }
  }

  const rows: RegionRow[] = [...labels.entries()]
    .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }))
    .map(([key, label]) => ({
      key,
      label,
      values: viable.map((candidate) => {
        const cell = fighterStatCells(candidate.stats).find((entry) => entry.key === key);
        return cell ? cell.value : null;
      }),
    }));

  return {
    columns: viable.map((candidate) => ({ candidate, recommended: recommendedIds.has(candidate.id) })),
    rows,
    bestObserved,
  };
}

/* ------------------------------------------------------------------ */
/* Small presentational helpers                                        */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Strategy families (baseline diff, never the raw candidate list)      */
/* ------------------------------------------------------------------ */

/**
 * The scenario fields this page reads to describe the shape of a build. `candidate.scenario` is
 * typed `unknown` because the host owns it, so only these fields are read - and every one of them
 * is optional: a field the runner did not record is never treated as a difference.
 */
type ScenarioUnitView = {
  name?: string | null;
  human?: boolean | null;
  monsterId?: number | null;
  skills?: unknown;
  invocationLevels?: unknown;
};

type ScenarioView = {
  encounterId?: unknown;
  ownUnits?: ScenarioUnitView[] | null;
  holyHerbStock?: unknown;
  inputs?: unknown;
};

function scenarioView(candidate: Candidate | null | undefined): ScenarioView {
  const value = candidate?.scenario;
  return value && typeof value === "object" ? (value as ScenarioView) : {};
}

/** The party in the order the scenario recorded it. */
function scenarioUnits(candidate: Candidate | null | undefined): ScenarioUnitView[] {
  const units = scenarioView(candidate).ownUnits;
  if (!Array.isArray(units)) return [];
  return units.filter((unit) => Boolean(unit) && typeof unit === "object");
}

/**
 * One key per unit, disambiguated by occurrence so two units sharing a name stay two units. The key
 * is what the diff matches on, so a unit that changed slot still matches its baseline counterpart.
 */
function unitKeys(units: ScenarioUnitView[]): Array<{ key: string; name: string }> {
  const seen = new Map<string, number>();
  return units.map((unit, index) => {
    const name = unit.name ?? (unit.monsterId != null ? `Monster ${unit.monsterId}` : `slot ${index + 1}`);
    const count = (seen.get(name) ?? 0) + 1;
    seen.set(name, count);
    return { key: count === 1 ? name : `${name} (${count})`, name };
  });
}

/** Text for one scenario value: numbers and strings verbatim, a list comma-joined, else compact JSON. */
function aspectValueText(value: unknown): string {
  if (value === undefined) return "not recorded";
  if (value === null) return "none";
  if (Array.isArray(value)) return value.length ? value.map((entry) => aspectValueText(entry)).join(",") : "empty";
  if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function sameAspectValue(left: unknown, right: unknown): boolean {
  return aspectValueText(left) === aspectValueText(right);
}

/** One aspect in which a build differs from its encounter's baseline, with the words for it. */
type ChangeAspect = { signal: string; sentence: string };

/**
 * The aspects in which one candidate's scenario differs from the baseline of its own encounter.
 *
 * These are exactly the axes the runner's legal mutations move: party membership, formation order
 * inside the party, one unit's skill activation order (and invocation levels), and the consumable
 * plan. Weapon, equipment and supplied stat values are deliberately not part of this diff, and the
 * card says so rather than implying the diff covers them.
 */
function changeAspects(candidate: Candidate, baseline: Candidate | null): ChangeAspect[] {
  const aspects: ChangeAspect[] = [];
  if (!baseline) return aspects;
  const units = scenarioUnits(candidate);
  const baseUnits = scenarioUnits(baseline);
  const keys = unitKeys(units);
  const baseKeys = unitKeys(baseUnits);
  const positionOf = new Map(keys.map((entry, index) => [entry.key, index]));
  const basePositionOf = new Map(baseKeys.map((entry, index) => [entry.key, index]));

  const added = keys.filter((entry) => !basePositionOf.has(entry.key)).map((entry) => entry.key);
  const removed = baseKeys.filter((entry) => !positionOf.has(entry.key)).map((entry) => entry.key);
  if (added.length || removed.length) {
    const parts: string[] = [];
    if (removed.length) parts.push(`removed ${removed.join(", ")}`);
    if (added.length) parts.push(`added ${added.join(", ")}`);
    aspects.push({
      signal: "party",
      sentence: `party ${parts.join("; ")} (${baseUnits.length} -> ${units.length} units)`,
    });
  } else if (keys.some((entry, index) => baseKeys[index]?.key !== entry.key)) {
    const moves = keys
      .map((entry, index) => ({ key: entry.key, from: basePositionOf.get(entry.key) ?? index, to: index }))
      .filter((move) => move.from !== move.to);
    const shown = moves.slice(0, 3).map((move) => `${move.key} moved from slot ${move.from + 1} to slot ${move.to + 1}`);
    if (moves.length > shown.length) shown.push(`${moves.length - shown.length} more unit(s) moved`);
    aspects.push({ signal: "formation", sentence: `formation: ${shown.join("; ")}` });
  }

  for (const entry of keys) {
    const index = positionOf.get(entry.key) ?? 0;
    const baseIndex = basePositionOf.get(entry.key);
    if (baseIndex === undefined) continue;
    const unit = units[index];
    const baseUnit = baseUnits[baseIndex];
    const orderChanged = !sameAspectValue(unit.skills, baseUnit.skills);
    const levelsChanged = !sameAspectValue(unit.invocationLevels, baseUnit.invocationLevels);
    if (!orderChanged && !levelsChanged) continue;
    const parts: string[] = [];
    if (orderChanged) parts.push(`skill order ${aspectValueText(baseUnit.skills)} -> ${aspectValueText(unit.skills)}`);
    if (levelsChanged) {
      parts.push(`invocation levels ${aspectValueText(baseUnit.invocationLevels)} -> ${aspectValueText(unit.invocationLevels)}`);
    }
    aspects.push({ signal: `skills:${entry.key}`, sentence: `${entry.key} ${parts.join("; ")}` });
  }

  const view = scenarioView(candidate);
  const baseView = scenarioView(baseline);
  const herbChanged = !sameAspectValue(view.holyHerbStock, baseView.holyHerbStock);
  const planChanged = !sameAspectValue(view.inputs, baseView.inputs);
  if (herbChanged || planChanged) {
    const parts: string[] = [];
    if (herbChanged) parts.push(`holy herb stock ${aspectValueText(baseView.holyHerbStock)} -> ${aspectValueText(view.holyHerbStock)}`);
    if (planChanged) parts.push(`items/inputs ${aspectValueText(baseView.inputs)} -> ${aspectValueText(view.inputs)}`);
    aspects.push({ signal: "consumables", sentence: `consumables: ${parts.join("; ")}` });
  }

  return aspects;
}

const UNCHANGED_SIGNATURE = "unchanged";

function changeSignature(aspects: ChangeAspect[]): string[] {
  if (!aspects.length) return [UNCHANGED_SIGNATURE];
  return aspects.map((aspect) => aspect.signal).sort((left, right) => left.localeCompare(right));
}

/**
 * The encounter's baseline: its supplied candidate, preferring one whose label says "baseline",
 * otherwise the first supplied candidate for that encounter. Null when the encounter has none.
 */
function baselineCandidate(candidates: Candidate[], encounterId: number | null): Candidate | null {
  const supplied = candidates.filter(
    (candidate) => candidate.source === "supplied" && encounterIdOf(candidate) === encounterId,
  );
  const named = supplied.find((candidate) => candidate.label.toLowerCase().includes("baseline"));
  return named ?? supplied[0] ?? null;
}

/**
 * The family's difference sentence. It always names the field that changed, or says plainly that
 * nothing in the compared fields changed - "this build is different" alone is never shown.
 */
function differenceSentence(
  aspects: ChangeAspect[],
  baseline: Candidate | null,
  encounterId: number | null,
  isBaseline = false,
): string {
  if (!baseline) {
    const scope = encounterId === null ? "this encounter" : `encounter ${encounterId}`;
    return `no supplied baseline was recorded for ${scope}, so this family could not be diffed against one`;
  }
  if (!aspects.length) {
    if (isBaseline) {
      return "this is the encounter's supplied baseline build itself - there is nothing to diff against, so this family is the reference the others are compared with";
    }
    return "unchanged build vs the baseline - same party, same slots, same skill orders/levels and same consumable plan in every field this diff covers";
  }
  return aspects.map((aspect) => aspect.sentence).join(" · ");
}

type StrategyFamily = {
  /** (encounter, the runner's own behaviour cell, sorted change signature) - the family key. */
  key: string;
  encounterId: number | null;
  behaviour: string | null;
  signature: string[];
  title: string;
  difference: string;
  baseline: Candidate | null;
  /** Every candidate in this family, best-measured first. */
  members: Candidate[];
  representative: Candidate;
  representativeChests: ChestMean | null;
  representativeWin: WinMean | null;
  representativeInterval: [number, number] | undefined;
  /** True only with a complete, comparable bank AND a win lower bound at the recommended bar. */
  tested: boolean;
  /** Which part is missing while the family stays provisional. */
  stateDetail: string;
  /** The family's own records and work counts, separate from the encounter-wide lifetime records. */
  metrics: ReturnType<typeof familyMetrics>;
};

/** Most chests per run, then higher win lower bound, then more samples, then a complete bank. */
function compareRepresentative(left: Candidate, right: Candidate): number {
  const chests = (candidate: Candidate) => candidateChests(candidate)?.mean ?? -Infinity;
  const lower = (candidate: Candidate) => candidateWin(candidate)?.lower ?? -Infinity;
  const samples = (candidate: Candidate) => candidateChests(candidate)?.samples ?? -1;
  return (
    chests(right) - chests(left) ||
    lower(right) - lower(left) ||
    samples(right) - samples(left) ||
    Number(isCompleteSelection(right)) - Number(isCompleteSelection(left)) ||
    (right.selection?.n ?? 0) - (left.selection?.n ?? 0)
  );
}

function summaryForScope(candidate: Candidate, scope: ChestScope): Summary | null {
  if (scope === "selection") return candidate.selection;
  if (scope === "validation") return candidate.validation;
  return candidate.discovery;
}

/** Short family title: the runner's own behavioural cell plus the aspect(s) that changed. */
function strategyFamilyTitle(behaviour: string | null, signature: string[]): string {
  const changed = signature.map((signal) => {
    if (signal === UNCHANGED_SIGNATURE) return "baseline build, unchanged";
    if (signal.startsWith("skills:")) return `skill order (${signal.slice("skills:".length)})`;
    return signal;
  });
  return `${familyDetail(behaviour)} — ${changed.join(" + ")}`;
}

function strategyFamilyKey(encounterId: number | null, behaviour: string | null, signature: string[]): string {
  const encounter = encounterId === null ? "no encounter" : `encounter ${encounterId}`;
  return [encounter, familyDetail(behaviour), signature.join("+")].join(" | ");
}

/**
 * "Sufficiently tested" needs the representative's complete, comparable selection bank AND a win
 * 95% lower bound at the recommended bar. Anything else is provisional, and the missing part is
 * named rather than left to the reader.
 */
function familyTestState(representative: Candidate): { tested: boolean; detail: string } {
  const selection = representative.selection;
  const lower = selection?.winInterval?.[0];
  const chests = candidateChests(representative);
  /*
   * Two different questions, kept apart:
   *
   *  * "is this measured well enough to trust the number?" - answered by the sample: a complete,
   *    comparable bank over the shared seed bank, plus how wide the chest mean actually is at 95%.
   *  * "does it clear the archive's win gate?" - a separate, stricter eligibility rule, and one the
   *    search rarely meets at a hard encounter. Reporting a low win rate as "not sufficiently
   *    tested" would conflate the two and make every family look unmeasured when it is in fact
   *    measured to a stated precision.
   */
  const chestPrecision =
    chests?.mean != null && selection?.chestSE != null
      ? `; chest mean ±${formatNumber(1.96 * selection.chestSE, 2)} at 95% over ${formatInt(selection.chestSampleCount ?? 0)} resolved runs`
      : "";
  const winGate =
    typeof lower === "number" && Number.isFinite(lower)
      ? lower >= RECOMMENDED_WIN_LOW
        ? `; win 95% lower bound ${formatNumber(lower, 2)} clears the ${RECOMMENDED_WIN_LOW.toFixed(2)} archive gate`
        : `; win 95% lower bound ${formatNumber(lower, 2)} is below the ${RECOMMENDED_WIN_LOW.toFixed(2)} archive gate`
      : "";
  if (!selection || (selection.n ?? 0) === 0) {
    return { tested: false, detail: "provisional — screening only: the representative has no selection runs yet" };
  }
  if (!isCompleteSelection(representative)) {
    if (selection.n < SELECTION_RUNS) {
      return {
        tested: false,
        detail: `provisional — partial bank: ${formatInt(selection.n)}/${SELECTION_RUNS} selection runs`,
      };
    }
    return {
      tested: false,
      detail: `provisional — bank not comparable: ${formatInt(selection.n)}/${SELECTION_RUNS} runs were not drawn over the same seeds`,
    };
  }
  return {
    tested: true,
    detail: `sufficiently sampled — complete comparable ${SELECTION_RUNS}-run bank${chestPrecision}${winGate}`,
  };
}

/**
 * Collapse the raw candidate list into strategy families, keyed by (encounter, the runner's own
 * behaviour cell, sorted change signature). Every candidate sharing all three is one family, which
 * is what turns twenty near-identical mutations into one row with twenty members.
 */
/**
 * A family's own numbers: the best any of its members reached, and how much work the family has had.
 * These are FAMILY records, deliberately separate from the encounter-wide lifetime records in the
 * Encounter Overview, which span every strategy tried for that fight.
 */
function familyMetrics(members: Candidate[]) {
  let earned: number | null = null;
  let potential: number | null = null;
  let attempts = 0;
  let wins = 0;
  let losses = 0;
  let noVerdict = 0;
  let consumables = 0;
  for (const member of members) {
    const scopes = [member.discovery, member.validation];
    attempts += scopes.reduce((total, scope) => total + (scope?.n ?? 0), 0);
    wins += scopes.reduce((total, scope) => total + (scope?.wins ?? 0), 0);
    losses += scopes.reduce((total, scope) => total + (scope?.losses ?? 0), 0);
    noVerdict += scopes.reduce((total, scope) => total + (scope?.censored ?? 0), 0);
    if (member.bestChestsEarned != null) {
      earned = earned == null ? member.bestChestsEarned : Math.max(earned, member.bestChestsEarned);
    }
    if (member.bestPotentialChests != null) {
      potential =
        potential == null ? member.bestPotentialChests : Math.max(potential, member.bestPotentialChests);
    }
    const scenario = (member.scenario ?? {}) as Record<string, unknown>;
    if (scenario.inputs || scenario.items || scenario.itemStock) consumables += 1;
  }
  const resolved = wins + losses;
  return {
    earned,
    potential,
    attempts,
    wins,
    losses,
    noVerdict,
    consumableMembers: consumables,
    winRate: resolved ? wins / resolved : null,
  };
}

function buildStrategyFamilies(candidates: Candidate[]): StrategyFamily[] {
  const baselines = new Map<string, Candidate | null>();
  const groups = new Map<
    string,
    { encounterId: number | null; behaviour: string | null; members: Candidate[]; baseline: Candidate | null }
  >();
  for (const candidate of candidates) {
    const encounterId = encounterIdOf(candidate);
    const baselineKey = String(encounterId);
    if (!baselines.has(baselineKey)) baselines.set(baselineKey, baselineCandidate(candidates, encounterId));
    const baseline = baselines.get(baselineKey) ?? null;
    const key = strategyFamilyKey(encounterId, candidate.family, changeSignature(changeAspects(candidate, baseline)));
    const group = groups.get(key);
    if (group) group.members.push(candidate);
    else groups.set(key, { encounterId, behaviour: candidate.family, members: [candidate], baseline });
  }

  const families: StrategyFamily[] = [];
  for (const [key, group] of groups) {
    const members = [...group.members].sort(compareRepresentative);
    const representative = members[0];
    const aspects = changeAspects(representative, group.baseline);
    const state = familyTestState(representative);
    const win = candidateWin(representative);
    const signature = changeSignature(aspects);
    families.push({
      key,
      encounterId: group.encounterId,
      behaviour: group.behaviour,
      signature,
      title: strategyFamilyTitle(group.behaviour, signature),
      difference: differenceSentence(aspects, group.baseline, group.encounterId, representative.id === group.baseline?.id),
      baseline: group.baseline,
      members,
      representative,
      representativeChests: candidateChests(representative),
      representativeWin: win,
      representativeInterval: win ? summaryForScope(representative, win.scope)?.winInterval : undefined,
      tested: state.tested,
      stateDetail: state.detail,
      metrics: familyMetrics(members),
    });
  }
  // Put complete comparable banks first, then rank by mean yield. A single exceptional battle
  // must not outrank a strategy that earns more across the shared selection seeds.
  return families.sort((left, right) =>
    Number(right.tested) - Number(left.tested) ||
    (right.representativeChests?.mean ?? -1) - (left.representativeChests?.mean ?? -1) ||
    (right.metrics.winRate ?? -1) - (left.metrics.winRate ?? -1) ||
    (right.metrics.earned ?? -1) - (left.metrics.earned ?? -1) ||
    right.metrics.attempts - left.metrics.attempts ||
    left.key.localeCompare(right.key));
}

/**
 * The representative's best stored run: most chests, then a win over an unresolved run, then the
 * most survivors. Null when the runner stored nothing for that build.
 */
function bestStoredRunLabel(candidate: Candidate | null | undefined): string | null {
  const examples: Record<string, Example> = candidate?.examples ?? {};
  const entries = Object.entries(examples);
  if (!entries.length) return null;
  const chests = (example: Example) => {
    const reading = chestReading(example);
    return reading.known ? reading.count : -1;
  };
  const outcomeRank = (example: Example) => {
    const outcome = runOutcome(example);
    return outcome === "win" ? 2 : outcome === "unresolved" ? 1 : 0;
  };
  const survivors = (example: Example) =>
    typeof example.survivors === "number" && Number.isFinite(example.survivors) ? example.survivors : -1;
  const sorted = [...entries].sort(
    (left, right) =>
      chests(right[1]) - chests(left[1]) ||
      outcomeRank(right[1]) - outcomeRank(left[1]) ||
      survivors(right[1]) - survivors(left[1]) ||
      left[0].localeCompare(right[0]),
  );
  return sorted[0][0];
}

/** Weapon and reach per fighter, from the runner's own prepared stats. */
function weaponSummary(stats: Record<string, FighterStat> | undefined): string {
  const rows = Object.entries(stats ?? {}).map(([fighter, stat]) => {
    const weapon = stat?.weaponId == null ? "—" : String(stat.weaponId);
    const range = typeof stat?.weaponRange === "number" ? String(stat.weaponRange) : "—";
    return `${fighter} weapon ${weapon}, range ${range}`;
  });
  return rows.length ? rows.join(" · ") : "none recorded";
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="font-mono text-sm font-semibold tabular-nums">{value}</div>
      {hint ? <div className="text-[10px] text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

/**
 * One runtime reading in the top bar. The same boxed shape as `StatTile` (the "what is known
 * instead" tiles), with a larger value because these are the primary numbers on the page.
 */
function PerfTile({ label, value, hint, title, hook }: {
  label: string;
  value: string;
  hint?: string;
  title?: string;
  hook?: string;
}) {
  const attributes = hook ? { [hook]: "" } : {};
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2" title={title} {...attributes}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="font-mono text-lg font-semibold leading-tight tabular-nums">{value}</div>
      {hint ? <div className="text-[10px] text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

function ProgressRow({ label, value }: { label: string; value: number }) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span className="tabular-nums">{clamped.toFixed(0)}%</span>
      </div>
      <Progress value={clamped} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Threshold and range probes (host-measured)                          */
/* ------------------------------------------------------------------ */

/**
 * The axes the optimiser holds as established combat inputs. Only these are offered in the primary
 * select. Intelligence is deliberately absent: it is reachable only behind the explicit "measure an
 * unestablished input" affordance and is then sent as `unestablished: true`, so the host keeps the
 * authority on what may be measured and why it refuses.
 */
const PROBE_AXES = ["atk", "def", "hp", "mp", "spd", "lck", "dex", "herbs"] as const;
const UNESTABLISHED_PROBE_AXES = ["int"] as const;
const NO_SECOND_AXIS = "none";
/**
 * The combat statistics a fine-tuning program may sweep, in the host's own priority order
 * (`strategy_finetune.COMBAT_STAT_PRIORITY`: hp, mp, vig, atk, def, spd, lck, dex, int). Gathering,
 * MOV and Love are deliberately absent - no recovered damage or hit formula reads them, so the host
 * refuses those axes - and `vig` is the native parameter 12 "Energy". Intelligence is offered like
 * any other stat; the host accepts or refuses it per unit and its refusal is shown as returned.
 */
const FINE_TUNE_AXES = ["hp", "mp", "vig", "atk", "def", "spd", "lck", "dex", "int"] as const;
/** The unit select's "let the host choose" option: `fine_tune` resolves the unit when none is sent. */
const FINE_TUNE_UNIT_AUTO = "auto";

/**
 * A display name for an axis code, resolved from the canonical stat/parameter table rather than a
 * second local mapping. An axis with no canonical entry (herbs) is shown as its own code.
 */
function axisName(axis: string | null | undefined, savedLabel?: string | null): string {
  if (!axis) return savedLabel ?? "—";
  const key = canonicalStatKey(axis);
  // Display aliases only: preserve native IDs, stored labels and request keys.
  if (key === "spd" || key === "vig") return statIconKey(key)!;
  if (savedLabel) return savedLabel;
  const id = (STAT_PARAMETER_IDS as Record<string, number | undefined>)[axis];
  return (id == null ? undefined : PARAMETER_NATIVE_NAMES[id]) ?? axis;
}

/** Established means the axis is on the host's confirmed combat-input list. */
function axisIsEstablished(axis: string | null | undefined): boolean {
  return axis != null && (PROBE_AXES as readonly string[]).includes(axis);
}

/**
 * A tuning dimension must be a combat statistic. Gathering, MOV and Love have no recovered combat
 * reader, so a host payload that names one is never rendered as a tested/tuning axis; spellings are
 * normalised through the canonical stat table first, so `mov`, `move` and `movement` all match.
 */
function isCombatTuningAxis(axis: string | null | undefined): boolean {
  if (!axis) return false;
  return !(ADVANCED_PARAMETER_KEYS as readonly string[]).includes(canonicalStatKey(axis));
}

/** A grid probe carries a second axis and a `kind`; a single-axis ladder carries neither. */
function probeIsGrid(probe: Probe): boolean {
  return probe.kind === "grid" || probe.axis2 != null;
}

/** Render a host-published value exactly as text. Nothing is derived from it. */
function probeValue(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? formatNumber(value, 3) : value;
}

/**
 * Chests per run with the 95% half-width the host's own standard error implies. A chest mean is
 * never shown bare while a standard error exists, and a missing standard error is labelled.
 */
function probeChests(mean: number | null | undefined, se: number | null | undefined): string {
  if (typeof mean !== "number" || !Number.isFinite(mean)) return "—";
  if (typeof se !== "number" || !Number.isFinite(se)) return `${formatNumber(mean, 3)} — no standard error reported`;
  return `${formatNumber(mean, 3)} ± ${formatNumber(1.96 * se, 3)} (95%)`;
}

/** The host's paired comparison at one rung, exactly as measured, or the reason it is absent. */
function probeDelta(delta: ProbeDelta | null | undefined): string {
  if (!delta) return "no paired comparison — too few shared seeds";
  const interval =
    typeof delta.lower === "number" && typeof delta.upper === "number"
      ? `95% ${formatNumber(delta.lower, 3)} to ${formatNumber(delta.upper, 3)}`
      : "95% interval not reported";
  return `${formatNumber(delta.mean, 3)} (${interval}), t = ${formatNumber(delta.t, 2)}, n = ${formatInt(delta.n ?? null)}`;
}

/**
 * The span of confirmed working points. Failures inside that span remain visible in the point table
 * and host statement, so this never claims that untested values between the endpoints work.
 */
function probeRange(low: number | null, high: number | null): string {
  if (low !== null && high !== null) return `on this ladder: ${formatNumber(low, 3)} to ${formatNumber(high, 3)} (tested points only)`;
  return "no working value confirmed on this ladder";
}

/** Viable, not viable, or explicitly unmeasured. A null rung is never given a verdict here. */
function ProbeVerdict({ viable }: { viable: boolean | null | undefined }) {
  if (viable !== true && viable !== false) {
    return (
      <span className="text-[11px] text-muted-foreground" data-probe-viable="not-measured">
        not measured yet
      </span>
    );
  }
  return (
    <span data-probe-viable={viable ? "viable" : "not-viable"}>
      <ToneBadge category={viable ? "success" : "warning"}>{viable ? "viable" : "not viable"}</ToneBadge>
    </span>
  );
}

/** The measured rungs, shown as measured: value, effective stat, runs, chests, delta, verdict. */
function ProbePoints({ probe }: { probe: Probe }) {
  const grid = probeIsGrid(probe);
  const points = probe.points ?? [];
  const first = axisName(probe.axis, probe.axisLabel);
  const second = axisName(probe.axis2, probe.axis2Label);
  if (!points.length) {
    return (
      <p className="text-[11px] text-muted-foreground" data-probe-points="empty">
        The host published no measured points for this probe.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto" data-probe-points>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{first}</TableHead>
            {grid ? <TableHead>{second}</TableHead> : null}
            <TableHead>Effective stat used</TableHead>
            {grid ? <TableHead>Effective {second}</TableHead> : null}
            <TableHead>Runs</TableHead>
            <TableHead>Chests per run</TableHead>
            <TableHead>Paired delta vs reference</TableHead>
            <TableHead>Verdict</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {points.map((point, index) => (
            <TableRow
              key={`${probe.pivotId}-${probe.axis}-${point.value ?? "?"}-${index}`}
              data-probe-point={probeValue(point.value)}
            >
              <TableCell className="font-mono text-xs tabular-nums">{probeValue(point.value)}</TableCell>
              {grid ? <TableCell className="font-mono text-xs tabular-nums">{probeValue(point.value2)}</TableCell> : null}
              <TableCell className="font-mono text-xs tabular-nums">{formatNumber(point.effective)}</TableCell>
              {grid ? <TableCell className="font-mono text-xs tabular-nums">{formatNumber(point.effective2)}</TableCell> : null}
              <TableCell className="text-xs tabular-nums">{formatInt(point.runs ?? null)}</TableCell>
              <TableCell className="text-xs tabular-nums">{probeChests(point.chestMean, point.chestSE)}</TableCell>
              <TableCell className="text-xs tabular-nums">{probeDelta(point.delta)}</TableCell>
              <TableCell>
                <ProbeVerdict viable={point.viable} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Per-row grid thresholds, keyed by the first axis value, with the host's bracketing verdict. */
function ProbeThresholds({ probe }: { probe: Probe }) {
  const rows = Object.entries(probe.thresholds ?? {});
  const first = axisName(probe.axis, probe.axisLabel);
  return (
    <div className="space-y-1" data-probe-thresholds>
      <div className="text-[11px] font-medium text-foreground/90">Per-row thresholds, keyed by {first}</div>
      {rows.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{first}</TableHead>
              <TableHead>Threshold</TableHead>
              <TableHead>Bracketed</TableHead>
              <TableHead>Note</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(([row, entry]) => (
              <TableRow key={row} data-probe-threshold={row}>
                <TableCell className="font-mono text-xs tabular-nums">{row}</TableCell>
                <TableCell className="font-mono text-xs tabular-nums">{probeValue(entry?.threshold)}</TableCell>
                <TableCell
                  className="text-xs"
                  data-probe-threshold-bracketed={
                    entry?.bracketed === true ? "yes" : entry?.bracketed === false ? "no" : "not-reported"
                  }
                >
                  {entry?.bracketed === true ? "bracketed" : entry?.bracketed === false ? "not bracketed" : "not reported"}
                </TableCell>
                <TableCell className="text-xs">{entry?.note ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-[11px] text-muted-foreground">The host published no per-row thresholds for this grid.</p>
      )}
    </div>
  );
}

/**
 * The host's own interaction sentence, built from its fields and nothing else. A null interaction is
 * reported as not demonstrated rather than left to imply one.
 */
function ProbeInteractionRow({ probe }: { probe: Probe }) {
  const interaction = probe.interaction ?? null;
  if (!interaction) {
    return (
      <p className="text-[11px] text-muted-foreground" data-probe-interaction="none">
        The host demonstrated no interaction between the two axes of this grid, so none is implied here.
      </p>
    );
  }
  return (
    <div className="space-y-1" data-probe-interaction={interaction.kind ?? "reported"}>
      <p className="text-xs">
        {interaction.kind ? `${interaction.kind}: ` : ""}when {axisName(interaction.axis)} is{" "}
        {probeValue(interaction.fromValue)} the strategy needs {axisName(interaction.other)} at least{" "}
        {probeValue(interaction.fromThreshold)}
        {interaction.fromBracketed === false ? " (not bracketed)" : ""}; when {axisName(interaction.axis)} is{" "}
        {probeValue(interaction.toValue)} it needs at or below {probeValue(interaction.toThreshold)}
        {interaction.toBracketed === false ? " (not bracketed)" : ""}.
      </p>
      <p className="text-[11px] text-muted-foreground tabular-nums">
        difference {probeValue(interaction.difference)} · grid step {probeValue(interaction.gridStep)}
      </p>
    </div>
  );
}

/** One published analysis: the axis context, the measured rungs, the host's words, the grid extras. */
function ProbeAnalysis({ probe, lastProbe }: { probe: Probe; lastProbe: LastProbe | null }) {
  const grid = probeIsGrid(probe);
  const match =
    lastProbe &&
    lastProbe.pivotId === probe.pivotId &&
    lastProbe.axis === probe.axis &&
    (lastProbe.axis2 ?? null) === (probe.axis2 ?? null)
      ? lastProbe
      : null;
  const established =
    match && typeof match.established === "boolean"
      ? match.established
      : axisIsEstablished(probe.axis) && (probe.axis2 == null || axisIsEstablished(probe.axis2));
  const low = typeof probe.viableLow === "number" ? probe.viableLow : null;
  const high = typeof probe.viableHigh === "number" ? probe.viableHigh : null;
  return (
    <div
      className="space-y-3 rounded-md border p-3"
      data-probe={probe.pivotId}
      data-probe-axis={probe.axis}
      data-probe-axis2={probe.axis2 ?? ""}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">
          {grid
            ? `${axisName(probe.axis, probe.axisLabel)} × ${axisName(probe.axis2, probe.axis2Label)}`
            : axisName(probe.axis, probe.axisLabel)}
        </span>
        <Badge variant="secondary" className="font-mono text-[10px]">
          {grid ? `${probe.axis} × ${probe.axis2}` : probe.axis}
        </Badge>
        <span className="text-xs text-muted-foreground">unit {probe.unit ?? "not reported"}</span>
        <ToneBadge category={established ? "muted" : "warning"}>
          {established ? "established combat input" : "unestablished input — being explored"}
        </ToneBadge>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Pivot {probe.pivotLabel ?? probe.pivotId} · reference strategy measured over{" "}
        {formatInt(probe.referenceRuns ?? null)} runs
      </p>
      {!established ? (
        <p className="text-[11px]" data-probe-axis-note>
          This axis is not an established combat input; it was asked for explicitly and measured as an
          exploration. The host's reason: {match?.axisNote ?? "the host recorded no reason for this axis"}.
        </p>
      ) : null}
      <ProbePoints probe={probe} />
      <div className="space-y-1">
        <div className="text-[11px] font-medium text-foreground/90">Confirmed working point span</div>
        <p className="text-xs tabular-nums" data-probe-range>
          {probeRange(low, high)}
        </p>
      </div>
      {probe.boundaryAt ? (
        <p className="text-[11px] text-muted-foreground" data-probe-boundary>
          Measured transitions at {probe.boundaryAt}
        </p>
      ) : null}
      <div className="space-y-1">
        <div className="text-[11px] font-medium text-foreground/90">Host statement</div>
        <p className="text-xs" data-probe-statement>
          {probe.statement ?? "the host published no statement for this probe"}
        </p>
      </div>
      <p className="text-[11px] text-muted-foreground" data-probe-basis>
        Basis: {probe.basis ?? "the host published no basis for this probe"}
      </p>
      {grid ? (
        <>
          <ProbeThresholds probe={probe} />
          <ProbeInteractionRow probe={probe} />
        </>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Fine-tuning programs (frozen parent, one statistic at a time)        */
/* ------------------------------------------------------------------ */

/** The host's program status as a badge. An unknown status is shown verbatim, never guessed. */
function fineTuneStatus(status: string | null | undefined): { label: string; category: KaCategory } {
  if (status === "active") return { label: "active", category: "success" };
  if (status === "done") return { label: "done", category: "muted" };
  if (status === "blocked") return { label: "blocked", category: "warning" };
  if (status === "waiting") return { label: "waiting for focus", category: "warning" };
  return { label: status ? status : "unknown", category: "muted" };
}

/** The point's readiness: comparable at or above the readiness floor, provisional below it. */
function fineTuneReadiness(point: FineTuneEvidence, minimum: number | null | undefined): string {
  if (point.comparable === true) return "comparable";
  const resolved = typeof point.resolved === "number" ? point.resolved : null;
  const floor = typeof minimum === "number" ? minimum : null;
  if (resolved !== null && floor !== null) return `provisional — ${formatInt(resolved)} of ${formatInt(floor)}`;
  return "provisional";
}

/**
 * One tested value stated in plain language for the compact "Tested ranges" summary: the value, what
 * it did, the average earned there and this point's own sample count - never a bare number.
 */
function testedValueOutcome(point: FineTunePoint): string {
  const verdict =
    point.viable === true ? "works" : point.viable === false ? "fails" : "no verdict yet";
  const samples = typeof point.resolved === "number" ? `n=${formatInt(point.resolved)}` : "n not reported";
  const mean = point.meanEarned == null ? "no mean yet" : `mean ${formatMetric(point.meanEarned)}`;
  return `${probeValue(point.value)} ${verdict} · ${mean} · ${samples}`;
}

/** The compact known-working / known-failing / still-unmeasured line for one program's points. */
function testedVerdictLine(points: readonly FineTunePoint[]): string {
  const values = (verdict: boolean) =>
    points.filter((point) => point.viable === verdict).map((point) => probeValue(point.value));
  const working = values(true);
  const failing = values(false);
  const unknown = points
    .filter((point) => point.viable !== true && point.viable !== false)
    .map((point) => probeValue(point.value));
  const parts = [
    working.length ? `Known working: ${working.join(", ")}` : null,
    failing.length ? `Known failing: ${failing.join(", ")}` : null,
    unknown.length ? `Not measured yet: ${unknown.join(", ")}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "No tested value has a verdict yet.";
}

/** One measured point's verdict: comparable or provisional, plus the paired viable reading. */
function FineTunePointState({
  point,
  minimum,
}: {
  point: FineTuneEvidence;
  minimum: number | null | undefined;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span data-finetune-point-state={point.comparable === true ? "comparable" : "provisional"}>
        <ToneBadge category={point.comparable === true ? "success" : "warning"}>
          {fineTuneReadiness(point, minimum)}
        </ToneBadge>
      </span>
      <ProbeVerdict viable={point.viable} />
    </div>
  );
}

/**
 * The measured points of one program, shown as measured: the requested target and the effective stat
 * used, the point's own sample count against the readiness floor, its win rate with the Wilson
 * interval, its earned and loss-only potential means, and its paired difference against the
 * unchanged frozen parent. Each row links to its own child build so the full slot/equipment/skill
 * inspection and repeat simulation of that exact point stay reachable.
 */
function FineTunePoints({
  program,
  onOpenChild,
}: {
  program: FineTuneProgram;
  onOpenChild: (child: { candidateId: string; label: string }) => void;
}) {
  const points = program.points ?? [];
  const first = axisName(program.axis, program.axisLabel);
  if (!points.length) {
    return (
      <p className="text-[11px] text-muted-foreground" data-finetune-points="empty">
        The host has measured no point for this program yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto" data-finetune-points>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{first} target</TableHead>
            <TableHead>Effective stat used</TableHead>
            <TableHead>Resolved / required</TableHead>
            <TableHead>Win rate (95%)</TableHead>
            <TableHead>Mean earned</TableHead>
            <TableHead>Mean potential · loss-only</TableHead>
            <TableHead>vs unchanged parent</TableHead>
            <TableHead>State</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {points.map((point, index) => {
            const key = point.candidateId ?? `${program.id}-${point.value ?? index}`;
            const interval =
              point.winInterval && point.winInterval.length === 2
                ? `95% ${formatNumber(point.winInterval[0], 2)} to ${formatNumber(point.winInterval[1], 2)}`
                : null;
            return (
              <TableRow
                key={key}
                data-finetune-point={point.value ?? ""}
                data-finetune-point-candidate={point.candidateId ?? ""}
              >
                <TableCell className="text-xs tabular-nums">
                  {probeValue(point.value)}
                  {point.reason ? (
                    <span className="ml-1 text-[10px] text-muted-foreground">({point.reason})</span>
                  ) : null}
                </TableCell>
                <TableCell className="text-xs tabular-nums">{probeValue(point.effective)}</TableCell>
                <TableCell className="text-xs tabular-nums" data-finetune-samples>
                  {formatInt(point.resolved)} / {formatInt(program.minSeeds)}
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    runs {formatInt(point.runs)}
                  </span>
                </TableCell>
                <TableCell className="text-xs tabular-nums">
                  {formatPercent(point.winRate)}
                  {interval ? (
                    <span className="ml-1 text-[10px] text-muted-foreground">({interval})</span>
                  ) : null}
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">
                  {formatMetric(point.meanEarned)}{" "}
                  <span className="text-[10px] text-muted-foreground">n={formatInt(point.earnedSamples)}</span>
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">
                  {formatMetric(point.meanPotential)}{" "}
                  <span className="text-[10px] text-muted-foreground">n={formatInt(point.potentialSamples)}</span>
                </TableCell>
                <TableCell className="text-xs" data-finetune-paired>
                  {probeDelta(point.paired)}
                </TableCell>
                <TableCell>
                  <FineTunePointState point={point} minimum={program.minSeeds} />
                </TableCell>
                <TableCell>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!point.candidateId}
                    onClick={() =>
                      point.candidateId &&
                      onOpenChild({
                        candidateId: point.candidateId,
                        label: `${first} ${probeValue(point.value)} · ${program.parent.label ?? "parent"}`,
                      })
                    }
                    data-action="open-finetune-child"
                    data-finetune-child={point.candidateId ?? ""}
                  >
                    <Eye className="mr-1 h-3.5 w-3.5" /> Inspect
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

/**
 * The grid analyser's ordinals are `axis-1`/`axis-2`, which are the program's swept axis and its
 * declared interaction axis in that order. This names the ordinal with that declared axis; any other
 * value is left exactly as the host wrote it rather than guessed.
 */
function fineTuneInteractionAxis(
  code: string | null | undefined,
  program: FineTuneProgram,
): string | null {
  if (code === "axis-1") return program.axis;
  if (code === "axis-2") return program.grid?.axis2 ?? program.axis2 ?? null;
  return code ?? null;
}

/** The two-axis interaction the host demonstrated, named with the program's own declared axes. */
function FineTuneInteraction({ program }: { program: FineTuneProgram }) {
  const interaction = program.grid?.interaction ?? null;
  const gridSecond = program.grid?.axis2 ?? program.axis2 ?? null;
  if (!interaction) {
    return (
      <p className="text-[11px] text-muted-foreground" data-finetune-interaction="none">
        The host demonstrated no interaction between the two axes yet, so no boundary shift is implied
        here.
      </p>
    );
  }
  const first = fineTuneInteractionAxis(interaction.axis, program) ?? program.axis;
  const second = fineTuneInteractionAxis(interaction.other, program) ?? gridSecond;
  const requirement = (
    threshold: number | string | null | undefined,
    bracketed: boolean | null | undefined,
  ) =>
    bracketed === false
      ? `at or below ${probeValue(threshold)} (nothing lower was probed)`
      : `at least ${probeValue(threshold)}`;
  return (
    <div className="space-y-1" data-finetune-interaction={interaction.kind ?? "reported"}>
      <p className="text-xs">
        {interaction.kind ? `${interaction.kind}: ` : ""}when {axisName(first)} is{" "}
        {probeValue(interaction.fromValue)} the strategy needs {axisName(second)}{" "}
        {requirement(interaction.fromThreshold, interaction.fromBracketed)}; when {axisName(first)} is{" "}
        {probeValue(interaction.toValue)} it needs {axisName(second)}{" "}
        {requirement(interaction.toThreshold, interaction.toBracketed)}.
      </p>
      <p className="text-[11px] text-muted-foreground tabular-nums">
        requirement moves by {probeValue(interaction.difference)} · one grid step is{" "}
        {probeValue(interaction.gridStep)}
      </p>
    </div>
  );
}

/**
 * One fine-tuning program: the frozen parent it is grouped under, the one swept input, the measured
 * points, the measured boundary (only when the host bracketed one) and the interaction grid once it
 * exists. Everything is read from the host's own program view; an absent boundary or interaction is
 * reported as untested rather than filled in.
 */
function FineTuneProgramCard({
  program,
  onOpenChild,
}: {
  program: FineTuneProgram;
  onOpenChild: (child: { candidateId: string; label: string }) => void;
}) {
  const status = fineTuneStatus(program.waitingForFocus ? "waiting" : program.status);
  const baseline = program.baseline?.effective ?? null;
  const cells = program.cells ?? [];
  return (
    <div
      className="space-y-3 rounded-md border p-3"
      data-finetune-program={program.id}
      data-finetune-axis={program.axis}
      data-finetune-auto={program.auto ? "true" : "false"}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{axisName(program.axis, program.axisLabel)}</span>
        <Badge variant="secondary" className="font-mono text-[10px]">
          {program.axis}
        </Badge>
        {program.auto ? <ToneBadge category="muted">automatic</ToneBadge> : null}
        <span
          data-finetune-status={program.waitingForFocus ? "waiting" : program.status}
        >
          <ToneBadge category={status.category}>{status.label}</ToneBadge>
        </span>
        <span className="text-xs text-muted-foreground">unit {program.unit ?? "not reported"}</span>
      </div>
      <p className="text-[11px] text-muted-foreground" data-finetune-program-summary>
        Baseline {probeValue(baseline)} · direction {program.direction ?? "both"} · step{" "}
        {formatInt(program.step)} · targets{" "}
        {program.targets?.length
          ? program.targets.map((value) => probeValue(value)).join(", ")
          : "none requested"}{" "}
        · {formatInt(program.used?.points)} point(s) and{" "}
        {formatInt(program.used?.interactionCells)} interaction cell(s) of{" "}
        {formatInt(program.used?.children)}/
        {formatInt(
          typeof program.budget?.maxChildren === "number" ? program.budget.maxChildren : null,
        )}{" "}
        candidate(s) · readiness floor {formatInt(program.minSeeds)} resolved runs
      </p>
      {program.waitingForFocus ? (
        <p className="text-[11px]" data-finetune-waiting>
          Waiting for focus: this program only measures while encounter{" "}
          {formatInt(program.encounterId)} is the focused fight, so no attempt it would authorise can
          land on a fight you are not looking at.
        </p>
      ) : null}
      <FineTunePoints program={program} onOpenChild={onOpenChild} />
      <div className="space-y-1">
        <div className="text-[11px] font-medium text-foreground/90">Measured boundary</div>
        {program.boundary ? (
          <p className="text-xs tabular-nums" data-finetune-boundary>
            measured boundary between {probeValue(program.boundary.low)} and{" "}
            {probeValue(program.boundary.high)} (probed spacing {formatInt(program.boundary.resolution)})
            {program.resolved ? " · resolved to this program's resolution" : ""}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground" data-finetune-boundary="untested">
            no boundary is bracketed yet, so every value outside the measured points — and every gap
            between them — is untested.
          </p>
        )}
        {program.viableLow !== null && program.viableLow !== undefined ? (
          <p className="text-[11px] text-muted-foreground tabular-nums" data-finetune-viable-span>
            confirmed working points span {probeValue(program.viableLow)} to{" "}
            {probeValue(program.viableHigh)} (tested points only)
          </p>
        ) : null}
      </div>
      <div className="space-y-1">
        <div className="text-[11px] font-medium text-foreground/90">Host statement</div>
        <p className="text-xs" data-finetune-statement>
          {program.statement ?? "the host published no statement for this program"}
        </p>
        <p className="text-[11px] text-muted-foreground" data-finetune-basis>
          Basis: {program.basis ?? "the host published no basis for this program"}
        </p>
      </div>
      {program.grid || cells.length ? (
        <div className="space-y-1 rounded-md border border-dashed p-2" data-finetune-grid>
          <div className="text-[11px] font-medium text-foreground/90">
            Interaction grid · {axisName(program.axis)} ×{" "}
            {axisName(program.grid?.axis2 ?? program.axis2)}
          </div>
          <p className="text-[11px] text-muted-foreground" data-finetune-grid-statement>
            {program.grid?.statement ?? "the host published no grid statement yet"}
          </p>
          {cells.length ? (
            <p className="text-[11px] text-muted-foreground tabular-nums" data-finetune-grid-cells>
              measured cells:{" "}
              {cells
                .map(
                  (cell) =>
                    `${probeValue(cell.value)}×${probeValue(cell.value2)} = ${fineTuneReadiness(
                      cell,
                      program.minSeeds,
                    )}`,
                )
                .join("; ")}
            </p>
          ) : null}
          <FineTuneInteraction program={program} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * The investigation panel's concise answer to "what has been tested for this build, and which
 * worked?".
 *
 * It reads the same parent-anchored programs as the full Fine tuning section below (never the whole
 * candidate list) and states only what the host measured: each tuned unit/stat, the frozen baseline,
 * every tested value with its resolved runs, mean earned, win rate and comparable/provisional state,
 * plus the bracketed boundary and passing span when the host published one. It sits above the long
 * per-slot build detail so a reader arriving from an overview metric sees the working limits before
 * any recorded raw stat, and it never recalculates a range from a snapshot.
 */
function TestedStatValues({
  parentId,
  parentLabel,
  parentResident,
  selectedId,
  selectedLabel,
  onOpenParentRanges,
  programs,
  programsLoading,
  programsError,
  canReadPrograms,
  autoTune,
  onOpenAutoTuneParent,
}: {
  parentId: string | null;
  parentLabel: string;
  parentResident: boolean;
  selectedId: string | null;
  selectedLabel: string;
  onOpenParentRanges: (child: { candidateId: string; label: string }) => void;
  programs: FineTuneProgram[];
  programsLoading: boolean;
  programsError: string | null;
  canReadPrograms: boolean;
  autoTune: AutoTune | null;
  onOpenAutoTuneParent: (candidateId: string, label: string) => void;
}) {
  const autoParentId = autoTune?.parent?.candidateId ?? null;
  const autoForThisParent = Boolean(autoParentId && parentId && autoParentId === parentId);
  const autoElsewhere = Boolean(autoParentId && parentId && autoParentId !== parentId);
  /*
   * The panel can be opened on a tested point of a program. That child variant's own ranges are not
   * what these programs measured - they describe the program's frozen parent - so the summary names
   * the parent it speaks for and, when the reader opened a different build, keeps the two apart and
   * offers the parent's own ranges directly.
   */
  const selectedDiffers = Boolean(selectedId && parentId && selectedId !== parentId);
  const example = programs.find((program) => program.baseline?.effective != null) ?? programs[0] ?? null;
  const exampleValue =
    example && example.baseline?.effective != null
      ? `${axisName(example.axis)} ${probeValue(example.baseline.effective)}`
      : null;
  const measuredPoints = programs.reduce((total, program) => total + (program.points?.length ?? 0), 0);
  /** The build this section speaks for, named only when the panel resolved one. */
  const buildRef = parentId ? parentLabel : "this build";

  return (
    <div
      className="space-y-3 rounded-lg border-2 border-primary/30 bg-muted/20 p-3"
      data-investigation-tested-values
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold" data-tested-ranges-title>
            <FlaskConical className="h-4 w-4" /> Tested ranges
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground" data-tested-values-scope>
            {parentId ? (
              selectedDiffers ? (
                <>
                  Ranges the host measured on the parent build{" "}
                  <span className="font-mono text-foreground/90" data-tested-ranges-parent={parentId}>
                    {parentLabel}
                  </span>
                  . You opened{" "}
                  <span
                    className="font-mono text-foreground/90"
                    data-tested-ranges-selected={selectedId ?? undefined}
                  >
                    {selectedLabel}
                  </span>
                  , one tested point of these programs rather than the parent itself.
                </>
              ) : (
                <>
                  Ranges the host measured on{" "}
                  <span className="font-mono text-foreground/90" data-tested-ranges-parent={parentId}>
                    {parentLabel}
                  </span>
                  {" "}—{" "}
                  <span
                    className="font-mono text-foreground/90"
                    data-tested-ranges-selected={selectedId ?? undefined}
                  >
                    this exact build
                  </span>
                  .
                </>
              )
            ) : (
              "Ranges the host measured for this exact build."
            )}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <ToneBadge category={measuredPoints ? "success" : "muted"}>
            {measuredPoints ? `${formatInt(measuredPoints)} tested point(s)` : "no tested points"}
          </ToneBadge>
          {selectedDiffers && parentResident && parentId ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-auto max-w-full min-w-0 whitespace-normal break-words py-1 text-left text-[11px]"
              onClick={() => onOpenParentRanges({ candidateId: parentId, label: parentLabel })}
              data-action="open-tested-ranges-parent"
              data-tested-ranges-parent-link={parentId}
            >
              <Eye className="mr-1 h-3.5 w-3.5" /> Open parent ranges {parentLabel}
            </Button>
          ) : null}
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground" data-tested-values-caveat>
        Only the values listed here have been tested for this build. A raw recorded stat
        {exampleValue ? ` (like the baseline ${exampleValue})` : ""} is one build's snapshot, not a
        working range; a span of passing points is not proof that every value between them works. The
        attempt counts below are the resolved runs at each point, never the encounter's lifetime
        attempts.
      </p>
      <p className="text-[11px] text-muted-foreground" data-tested-values-more>
        The full program detail, the interaction grid and the manual sweep control are in{" "}
        <span className="font-medium text-foreground/90">Fine tuning</span> below.
      </p>

      {programsError ? (
        <p className="text-xs text-destructive" data-tested-values-error>
          {programsError}
        </p>
      ) : null}

      {programs.length ? (
        <div className="space-y-2" data-tested-values-programs>
          {programs.map((program) => {
            const status = fineTuneStatus(program.waitingForFocus ? "waiting" : program.status);
            const points = program.points ?? [];
            const baseline = program.baseline?.effective ?? null;
            return (
              <div
                key={program.id}
                className="space-y-1.5 rounded-md border bg-background/60 p-2"
                data-tested-values-program={program.id}
                data-tested-values-axis={program.axis}
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold">
                    {axisName(program.axis, program.axisLabel)}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    unit {program.unit ?? "not reported"}
                  </span>
                  {program.auto ? <ToneBadge category="muted">automatic</ToneBadge> : null}
                  <span data-tested-values-status={program.waitingForFocus ? "waiting" : program.status}>
                    <ToneBadge category={status.category}>{status.label}</ToneBadge>
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground tabular-nums" data-tested-values-baseline>
                  Baseline {probeValue(baseline)} · this build's recorded value, not a range
                </p>
                {points.length ? (
                  <p className="text-[11px] text-muted-foreground" data-tested-ranges-verdicts>
                    {testedVerdictLine(points)}
                  </p>
                ) : null}
                {points.length ? (
                  <p className="text-[11px] text-muted-foreground" data-tested-ranges-outcomes>
                    Tested: {points.map(testedValueOutcome).join(" · ")}
                  </p>
                ) : null}
                {points.length ? (
                  <ul className="space-y-1" data-tested-values-points>
                    {points.map((point, index) => (
                      <li
                        key={point.candidateId ?? `${program.id}-${point.value ?? index}`}
                        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded border bg-muted/30 px-2 py-1 text-[11px]"
                        data-tested-values-point={point.value ?? ""}
                        data-tested-ranges-selected-point={
                          selectedId && point.candidateId === selectedId ? "true" : "false"
                        }
                      >
                        <span className="font-mono font-semibold tabular-nums">
                          {probeValue(point.value)}
                        </span>
                        <span
                          data-tested-values-point-state={
                            point.comparable === true ? "comparable" : "provisional"
                          }
                        >
                          <ToneBadge category={point.comparable === true ? "success" : "warning"}>
                            {fineTuneReadiness(point, program.minSeeds)}
                          </ToneBadge>
                        </span>
                        <ProbeVerdict viable={point.viable} />
                        {selectedId && point.candidateId === selectedId ? (
                          <ToneBadge category="muted">your selected build</ToneBadge>
                        ) : null}
                        <span
                          className="text-muted-foreground tabular-nums"
                          data-tested-values-point-resolved
                        >
                          resolved {formatInt(point.resolved)} / {formatInt(program.minSeeds)}
                        </span>
                        <span
                          className="text-muted-foreground tabular-nums"
                          data-tested-values-point-earned
                        >
                          mean {formatMetric(point.meanEarned)}
                        </span>
                        <span className="text-muted-foreground tabular-nums" data-tested-values-point-win>
                          win {formatPercent(point.winRate)}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[11px] text-muted-foreground" data-tested-values-points="empty">
                    No stat value has been measured for this program yet, so this build has no tested
                    working limit here.
                  </p>
                )}
                <p className="text-[11px] text-muted-foreground tabular-nums" data-tested-values-limits>
                  {program.viableLow != null
                    ? `Passing tested points span ${probeValue(program.viableLow)} to ${probeValue(
                        program.viableHigh,
                      )} (tested points only, not a continuous range).`
                    : "No passing tested point is confirmed yet."}
                  {program.boundary
                    ? ` Measured boundary bracketed between ${probeValue(
                        program.boundary.low,
                      )} and ${probeValue(program.boundary.high)}.`
                    : ""}
                </p>
                {points.length ? (
                  <p className="text-[11px] text-muted-foreground" data-tested-ranges-gap>
                    Every value between these tested points, and every value outside them, is untested —
                    a gap the host has not measured, not a value known to fail.
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <div
          className="space-y-1 rounded-md border border-dashed bg-background/60 p-2"
          data-tested-values-empty
        >
          <p className="text-xs font-medium">No fine-tuning program has measured this exact build.</p>
          <p className="text-[11px] text-muted-foreground" data-tested-values-empty-detail>
            {programsLoading
              ? "Reading this build's fine-tuning programs…"
              : canReadPrograms
                ? `So there is no tested stat value and no working limit for ${buildRef}; the recorded stat values further down are one build's snapshot, not ranges.`
                : "This host build does not expose fine-tuning programs yet, so no tested value can be stated."}
          </p>
        </div>
      )}

      {autoForThisParent ? (
        <p className="text-[11px] text-muted-foreground" data-tested-values-auto-this>
          Automatic tuning is running for this build; its points appear above as ordinary tested
          values.
        </p>
      ) : null}

      {autoElsewhere ? (
        <div
          className="space-y-1.5 rounded-md border border-dashed bg-background/60 p-2"
          data-tested-values-auto-other
        >
          <p className="text-[11px] font-medium text-foreground/90">
            Automatic tuning is attached to a different build
          </p>
          <p className="text-[11px] text-muted-foreground">
            {autoTune?.parent?.label ?? shortId(autoParentId ?? "")} — any range it measures describes that
            build, not {parentLabel}.
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-auto max-w-full min-w-0 whitespace-normal break-words py-2 text-left"
            onClick={() =>
              autoParentId &&
              onOpenAutoTuneParent(autoParentId, autoTune?.parent?.label ?? shortId(autoParentId))
            }
            data-action="open-auto-tune-parent-quick"
            data-tested-values-auto-parent={autoParentId}
          >
            <Eye className="mr-1 h-3.5 w-3.5" /> Open auto-tuned parent{" "}
            {autoTune?.parent?.label ?? shortId(autoParentId ?? "")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The investigation panel's Fine tuning section, grouped under the frozen parent build.
 *
 * It reads the parent's own programs (never the whole candidate list), states the automatic-tuning
 * diagnostic from the host when nothing was started, and offers the one manual control: sweep another
 * statistic for this parent, optionally to an exact target value. The host owns every decision - the
 * unit it picks, whether the axis is accepted, whether the program may advance - and its refusal is
 * shown as returned.
 */
function RangeSummary({ programs, compact = false }: { programs: FineTuneProgram[]; compact?: boolean }) {
  return <section className="space-y-3 rounded-xl border p-4" data-range-summary>
    <h3 className="font-semibold">Where does this strategy work?</h3>
    {!programs.length ? <p className="text-sm text-muted-foreground">No measured stat sweep is available yet. Use Ranges & refinement to test how far a statistic can move before the strategy fails.</p> : (compact ? programs.slice(0, 3) : programs).map(program => {
      const tested = (program.points ?? []).filter(point => (point.runs ?? 0) > 0);
      const viable = tested.filter(point => point.viable);
      const failed = tested.filter(point => (point.losses ?? 0) > 0);
      const best = tested.filter(point => point.chestMean != null && point.ready).sort((a, b) => (b.chestMean ?? 0) - (a.chestMean ?? 0))[0];
      return <div key={program.id} className="space-y-1 border-t pt-3 text-xs">
        <p className="text-muted-foreground">Parent build: {program.parent.label ?? program.parent.candidateId}</p>
        <div className="font-semibold">{program.unit ?? "Selected unit"} · {axisName(program.axis, program.axisLabel)} <span className="font-normal text-muted-foreground">· {program.status}</span></div>
        <p>Best tested: {best ? `${formatNumber(best.value, 0)} (${formatNumber(best.chestMean, 2)} mean chests, ${formatInt(best.runs)} runs)` : "not established with sufficient evidence"}</p>
        <details><summary className="cursor-pointer text-muted-foreground">{tested.length} tested values · working values and failures</summary>
        <p className="mt-2">Working tested values: {viable.length ? viable.map(point => formatNumber(point.value, 0)).join(", ") : "not established"}</p>
        <p>Losses observed at: {failed.length ? failed.map(point => `${formatNumber(point.value, 0)} (${point.losses}/${point.runs})`).join(", ") : "none in measured points"}</p>
        </details><p className="text-muted-foreground">{program.statement ?? "Untested values and gaps remain unknown."} Tested points do not prove every value between them works.</p>
      </div>;
    })}
    {compact && programs.length > 3 ? <p className="text-xs text-muted-foreground">{programs.length - 3} more experiments in Ranges & refinement.</p> : null}
  </section>;
}

function FineTuneSection({
  parentId,
  parentLabel,
  resident,
  programs,
  programsLoading,
  programsError,
  canReadPrograms,
  autoTune,
  autoTunePublished,
  unitOptions,
  canStart,
  starting,
  startError,
  onStart,
  onOpenChild,
  onOpenAutoTuneParent,
  onFocusEncounter,
  backParent,
  onBackToParent,
}: {
  parentId: string | null;
  parentLabel: string;
  resident: boolean;
  programs: FineTuneProgram[];
  programsLoading: boolean;
  programsError: string | null;
  canReadPrograms: boolean;
  autoTune: AutoTune | null;
  autoTunePublished: boolean;
  unitOptions: string[];
  canStart: boolean;
  starting: boolean;
  startError: string | null;
  onStart: (request: { axis: string; unit: string; target: number | null; samples: number }) => void;
  onOpenChild: (child: { candidateId: string; label: string }) => void;
  onOpenAutoTuneParent: (candidateId: string, label: string) => void;
  onFocusEncounter: (encounterId: number) => void;
  backParent: { candidateId: string; label: string } | null;
  onBackToParent: () => void;
}) {
  const [axis, setAxis] = useState<string>(FINE_TUNE_AXES[1]);
  const [unit, setUnit] = useState<string>(FINE_TUNE_UNIT_AUTO);
  const [target, setTarget] = useState<string>("");
  const [samples, setSamples] = useState("1024");
  const samplesValue = Number(samples);
  const samplesInvalid = !Number.isSafeInteger(samplesValue) || samplesValue < 128 || samplesValue > 1_000_000;

  const targetValue = target.trim() ? Number(target.trim()) : null;
  const targetInvalid =
    target.trim() !== "" && (targetValue === null || !Number.isSafeInteger(targetValue));
  const waitingEncounter = programs.find((program) => program.waitingForFocus)?.encounterId ?? null;
  const autoParentId = autoTune?.parent?.candidateId ?? null;
  const autoForThisParent = Boolean(autoParentId && parentId && autoParentId === parentId);

  return (
    <div className="space-y-3 rounded-lg border p-3" data-investigation-finetune>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold">
            <FlaskConical className="h-4 w-4" /> Fine tuning
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            One frozen parent build, one combat statistic varied at a time, so every tested point is
            attributable to that single input. Grouped under{" "}
            <span className="font-mono text-foreground/90">{parentLabel}</span>.
          </p>
        </div>
        {backParent ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onBackToParent}
            data-action="back-to-finetune-parent"
            data-finetune-back-parent={backParent.candidateId}
          >
            <ChevronLeft className="mr-1 h-3.5 w-3.5" /> Back to parent {backParent.label}
          </Button>
        ) : null}
      </div>

      {programsError ? (
        <p className="text-xs text-destructive" data-finetune-programs-error>
          {programsError}
        </p>
      ) : null}

      {programs.length ? (
        <div className="space-y-3">
          {programs.map((program) => (
            <details key={program.id} className="rounded-lg border p-3"><summary className="cursor-pointer text-sm font-medium">{axisName(program.axis, program.axisLabel)} · {program.unit ?? "selected unit"} · {program.status} · experiment details</summary>
              <div className="mt-3"><FineTuneProgramCard program={program} onOpenChild={onOpenChild} /></div>
            </details>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground" data-finetune-programs-empty>
          {programsLoading
            ? "Reading this build's fine-tuning programs…"
            : canReadPrograms
              ? "The host has created no fine-tuning program for this build yet."
              : "This host build does not expose fine-tuning programs yet."}
        </p>
      )}

      {/* The host's own automatic-tuning diagnostic, or the reason it has none to give. */}
      {autoForThisParent ? (
        <div className="space-y-1 rounded-md border border-dashed p-2" data-finetune-autotune="this-parent">
          <p className="text-[11px] font-medium text-foreground/90">
            Automatic fine tuning is running for this build
          </p>
          <p className="text-[11px] text-muted-foreground">
            The host selected this build as the focused fight's strongest credibly measured strategy
            {autoTune?.parent?.meanEarned != null
              ? ` (mean earned ${formatMetric(autoTune.parent.meanEarned)} over ${formatInt(
                  autoTune.parent.resolved,
                )} resolved run(s))`
              : ""}{" "}
            and started its axis programs without a command.
          </p>
          <ul className="space-y-0.5 text-[11px] text-muted-foreground" data-finetune-autotune-axes>
            {(autoTune?.plannedAxes ?? [])
              .filter((entry) => isCombatTuningAxis(entry.axis))
              .map((entry) => (
                <li key={entry.axis} data-finetune-autotune-axis={entry.axis}>
                  {axisName(entry.axis, entry.axisLabel)} · unit {entry.unit ?? "not reported"} ·{" "}
                  {entry.programId
                    ? `${entry.status ?? "unknown"}, ${formatInt(entry.points)} point(s), ${formatInt(
                        entry.runs,
                      )} run(s)${entry.waitingForFocus ? " · waiting for focus" : ""}`
                    : `not started: ${entry.reason ?? "no reason published"}`}
                </li>
              ))}
          </ul>
          <p className="text-[11px] text-muted-foreground">
            An automatic point is an ordinary tested point; ask for an exact target below (for example
            MP 6500) if you want that value tested instead of the automatic first point.
          </p>
        </div>
      ) : autoTunePublished ? (
        <div className="space-y-1 rounded-md border border-dashed p-2" data-finetune-autotune="reason">
          <p className="text-[11px] font-medium text-foreground/90">
            Automatic fine tuning has not started for this build
          </p>
          <p className="text-[11px] text-muted-foreground" data-finetune-autotune-reason>
            {autoTune?.reason ??
              (autoParentId
                ? `the host is tuning a different build: ${
                    autoTune?.parent?.label ?? shortId(autoParentId)
                  }`
                : "the host published no reason")}
          </p>
          {autoParentId && autoParentId !== parentId ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-auto max-w-full min-w-0 whitespace-normal break-words py-2 text-left"
              onClick={() =>
                onOpenAutoTuneParent(autoParentId, autoTune?.parent?.label ?? shortId(autoParentId))
              }
              data-action="open-auto-tune-parent"
              data-auto-tune-parent={autoParentId}
            >
              <Eye className="mr-1 h-3.5 w-3.5" /> Open auto-tuned parent{" "}
              {autoTune?.parent?.label ?? shortId(autoParentId)}
            </Button>
          ) : null}
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground" data-finetune-autotune="unpublished">
          This host build does not publish automatic-tuning diagnostics yet, so whether automatic
          tuning started cannot be stated.
        </p>
      )}

      {waitingEncounter != null ? (
        <div className="flex flex-wrap items-center gap-2" data-finetune-focus-requirement>
          <span className="text-[11px] text-muted-foreground">
            Programs measure only while encounter {formatInt(waitingEncounter)} is the focused fight.
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!canStart || !resident}
            onClick={() => onFocusEncounter(waitingEncounter)}
            data-action="focus-finetune-encounter"
          >
            <Crosshair className="mr-1 h-3.5 w-3.5" /> Focus encounter {formatInt(waitingEncounter)}
          </Button>
        </div>
      ) : null}

      {resident ? (
        <div className="space-y-2 rounded-md border bg-muted/20 p-2" data-finetune-manual>
          <p className="text-[11px] font-medium text-foreground/90">Test another stat / target</p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label className="text-xs" htmlFor="finetune-axis">
                Statistic
              </Label>
              <Select value={axis} onValueChange={setAxis} disabled={!canStart || starting}>
                <SelectTrigger className="h-8 w-40 text-xs" id="finetune-axis" data-finetune-axis-select>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FINE_TUNE_AXES.map((option) => (
                    <SelectItem key={option} value={option}>
                      {axisName(option)} ({option})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs" htmlFor="finetune-unit">
                Unit
              </Label>
              <Select value={unit} onValueChange={setUnit} disabled={!canStart || starting}>
                <SelectTrigger className="h-8 w-40 text-xs" id="finetune-unit" data-finetune-unit-select>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={FINE_TUNE_UNIT_AUTO}>let the host choose</SelectItem>
                  {unitOptions.map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs" htmlFor="finetune-target">
                Target value (optional)
              </Label>
              <Input
                id="finetune-target"
                className="h-8 w-28 text-xs tabular-nums"
                inputMode="numeric"
                placeholder="e.g. 6500"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                disabled={!canStart || starting}
                data-finetune-target-input
              />
            </div>
            <Button
              type="button"
              size="sm"
              onClick={() => onStart({ axis, unit, target: targetInvalid ? null : targetValue, samples: samplesValue })}
              disabled={!canStart || starting || targetInvalid || samplesInvalid}
              data-action="start-finetune"
            >
              <Play className="mr-1 h-3.5 w-3.5" />
              {starting ? "Saving & starting…" : "Start refinement"}
            </Button>
            <div className="space-y-1"><Label className="text-xs" htmlFor="finetune-samples">Starting runs per value</Label>
              <Input id="finetune-samples" className="h-8 w-32 text-xs tabular-nums" inputMode="numeric" value={samples} onChange={event => setSamples(event.target.value)} disabled={!canStart || starting} />
            </div>
          </div>
          {samplesInvalid ? <p className="text-xs text-destructive">Choose 128–1,000,000 starting runs per value.</p> : null}
          <p className="text-[10px] text-muted-foreground" data-finetune-manual-hint>
            The host freezes this build as the parent, picks its own ladder and owns the refusal
            reason. Leave the target empty for legal minimum/maximum and broad interior values, followed by finer tests, or give an
            exact value (for example MP 6500) to have it tested first. A program only advances while
            its own encounter is focused. Uncertain comparisons receive larger banks; the starting count is not a confidence guarantee or a stopping cap. Start pauses and saves current battles, creates the program, focuses this encounter, and resumes the worker pool. Other strategies in the same encounter may also receive runs.
          </p>
          {targetInvalid ? (
            <p className="text-[11px] text-amber-600 dark:text-amber-500" data-finetune-target-invalid>
              Enter a whole number, or clear the field to let the host choose the points.
            </p>
          ) : null}
          {startError ? (
            <p className="text-[11px] text-destructive" data-finetune-start-error>
              {startError}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground" data-finetune-needs-restore>
          This target has no live build resident in the library, so no fine-tuning program can be
          started or read for it. A program needs the parent build restored or re-imported first.
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Strategy investigation (encounter ranking, records, fresh trials)    */
/* ------------------------------------------------------------------ */

const STRATEGY_SORT_ORDER: StrategySortMode[] = ["earned", "bestEarned", "winRate", "potential"];

const STRATEGY_SORT_LABEL: Record<StrategySortMode, string> = {
  earned: "Mean earned",
  bestEarned: "Best earned",
  winRate: "Win rate",
  potential: "Mean potential · loss-only",
};

/**
 * The host's source label for a range-test variant: a build the host created while probing a
 * strategy's stat range rather than a principal strategy the reader kept. These are ranked like
 * any other row, but the default list hides them so a fight crowded with probe rungs still shows
 * its real strategies first. The label is read from the host's own `source` field and is never
 * re-derived here.
 */
const PROBE_STRATEGY_SOURCE = "probe";

function isProbeStrategyRow(row: EncounterStrategyRow): boolean {
  return row.source === PROBE_STRATEGY_SOURCE;
}

/**
 * The name a reader sees for one exact build.
 *
 * The host derives `displayName` from lineage and the two stored scenarios — who made the change, the
 * unit and its actual old→new values, and the immediate parent's producer. It falls back to the
 * recorded `label` for a root build (which has no change to describe) and for an older host build that
 * does not publish the field, so the window never shows an empty name.
 */
function strategyRowName(row: EncounterStrategyRow): string {
  const derived = typeof row.displayName === "string" ? row.displayName.trim() : "";
  const recorded = typeof row.label === "string" ? row.label.trim() : "";
  return derived || recorded || `Unlabelled build · ${shortId(row.candidateId)}`;
}

/** The tooltip for one build: the structured name, then the ids and the recorded label it came from. */
function strategyRowTitle(row: EncounterStrategyRow): string {
  const lines = [strategyRowName(row)];
  if (row.nameDetail) lines.push(row.nameDetail);
  return lines.join(" — ");
}

/** Fresh-trial totals the control offers; the host is always called in chunks of at most eight. */
const FRESH_TRIAL_TOTALS = [128, 4096, 16384] as const;
/** The `Simulate # fights` count box: a player enters any total in this range, chunked by eight. */
const FRESH_TRIAL_MIN = 1;
const FRESH_TRIAL_MAX = 1_000_000_000;
const FRESH_TRIAL_DEFAULT = 4096;

const UNKNOWN_METRIC = "unknown";

/** A null host metric is unknown: never a zero, and never another build's number. */
function formatMetric(value: number | null | undefined, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? formatNumber(value, digits) : UNKNOWN_METRIC;
}

/**
 * The help text behind one average metric cell in the Encounter overview. It names what the mean is
 * taken over (resolved runs, or defeats carrying an opportunity), how much of the fight's history it
 * actually covers (`n` retained rows against the lifetime attempt count, so a rolled-off retention is
 * never implied to be the whole history) and which build and source produced it, so a thin or
 * probe-held mean is never read as a lifetime fact.
 */
function averageMetricTooltip(
  kind: "earned" | "potential",
  leader: AverageLeader,
  lifetimeAttempts: number | null,
  minSamples: number,
): string {
  const attempts = lifetimeAttempts ?? leader.attempts;
  const unit = kind === "earned" ? "resolved run(s)" : "defeat(s) with an opportunity";
  const metric =
    kind === "earned"
      ? "Mean chests released per resolved run (a defeat counts as the loss gate's zero)"
      : "Mean chest opportunity reached by a resolved defeat, averaged over defeats that carry one";
  const coverage =
    leader.samples === attempts
      ? `n=${formatInt(leader.samples)} measured ${unit}, the build's whole recorded attempt count`
      : `n=${formatInt(leader.samples)} measured ${unit} of the build's ${formatInt(attempts)} lifetime attempt(s)`;
  const completeness = leader.comparable
    ? "every recorded attempt has a resolved earned reading"
    : "not fully comparable: unresolved runs or missing historical readings";
  return `${metric}. ${coverage}; ${completeness}. At least ${minSamples} qualifying results are required. Highest measured mean of ${leader.label} (${leader.source}). This describes the measured outcomes.`;
}

/**
 * A small "how this is read" affordance for a card heading. The explanation is a real tooltip, so it
 * is reachable by hover and by keyboard focus and stays off the page instead of pushing the overview
 * down; the trigger is an icon-sized button so the heading stays one short line.
 */
function HelpTip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          data-optimizer-help
          className="inline-flex h-4 w-4 items-center justify-center rounded-full align-middle text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
        >
          <CircleHelp className="h-3.5 w-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm text-[11px] leading-snug">{children}</TooltipContent>
    </Tooltip>
  );
}

/**
 * The Encounter overview's "just improved" glow, as a set of lit cell keys.
 *
 * Baseline: nothing lights until the open library's first data has loaded (`ready`), and the first
 * observation after that only seeds the baseline. Every later observation is compared with the one
 * before it, so a record that was already in the library when the page opened never glows, and a
 * library switch re-seeds the baseline instead of lighting the new library's history.
 *
 * Timers: each improving cell gets its own {@link RECORD_HIGHLIGHT_MS} timeout; a later improvement
 * on the same cell clears and replaces it, so the window restarts from the latest improvement while
 * other cells keep their own countdowns. Expiry removes one cell and re-renders; it survives
 * rerenders because the timeouts live in a ref rather than in the render. Unmounting or switching
 * libraries clears every timeout and every glow.
 */
function useRecordHighlights(
  library: string | null | undefined,
  readings: RecordReadings,
  ready: boolean,
): Set<string> {
  const tracker = useRef<RecordHighlightTracker>(createRecordHighlightTracker(library));
  const timers = useRef(new Map<string, number>());
  const [version, bump] = useReducer((value: number) => value + 1, 0);

  const clearTimers = useCallback(() => {
    for (const timer of timers.current.values()) window.clearTimeout(timer);
    timers.current.clear();
  }, []);

  /** Publish a new glow set; the version bump is what makes a change or an expiry visible. */
  const publish = useCallback((next: RecordHighlightState) => {
    tracker.current = { ...tracker.current, state: next };
    bump();
  }, []);

  const expire = useCallback(
    (key: string) => {
      timers.current.delete(key);
      if (!(key in tracker.current.state)) return;
      const next = { ...tracker.current.state };
      delete next[key];
      publish(next);
    },
    [publish],
  );

  const arm = useCallback(
    (key: string) => {
      const existing = timers.current.get(key);
      if (existing !== undefined) window.clearTimeout(existing);
      timers.current.set(
        key,
        window.setTimeout(() => expire(key), RECORD_HIGHLIGHT_MS),
      );
    },
    [expire],
  );

  /** Clear every timer on unmount, so no glow outlives the panel that armed it. */
  useEffect(() => clearTimers, [clearTimers]);

  useEffect(() => {
    const before = tracker.current;
    const step = trackRecordHighlights(before, library, readings, ready, Date.now());
    tracker.current = step.tracker;
    // A library change drops the baseline and every glow, so a switch can never light a record the
    // previous library had already improved - or one that was already in the new library.
    if (step.reset) {
      clearTimers();
      publish(step.tracker.state);
      return;
    }
    for (const key of step.started) arm(key);
    if (step.started.length > 0 || step.tracker.state !== before.state) publish(step.tracker.state);
  }, [library, readings, ready, arm, clearTimers, publish]);

  return useMemo(() => new Set(Object.keys(tracker.current.state)), [version]);
}

/** The metric a sort mode ranks by; null means the host published nothing for it. */
function strategySortValue(row: EncounterStrategyRow, mode: StrategySortMode): number | null {
  if (mode === "bestEarned") return row.bestEarned;
  if (mode === "winRate") return row.winRate;
  if (mode === "potential") return row.meanPotential;
  return row.meanEarned;
}

/** How many runs back the ranked metric, so a thin mean is never confused with a well-sampled one. */
function strategySortSamples(row: EncounterStrategyRow, mode: StrategySortMode): number {
  if (mode === "potential") return row.potentialSamples;
  if (mode === "winRate") return row.wins + row.losses;
  return row.earnedSamples;
}

/**
 * The default ranking is mean earned over a comparable, well-sampled bank - never a single lucky
 * maximum. A build whose bank is comparable and whose mean covers at least the discovery sample
 * outranks a one-off; only the explicit "Best earned" mode orders by the maximum itself.
 */
function compareEncounterStrategies(
  left: EncounterStrategyRow,
  right: EncounterStrategyRow,
  mode: StrategySortMode,
): number {
  if (mode === "earned") {
    const leftReady = left.comparable && left.earnedSamples >= DISCOVERY_RUNS ? 1 : 0;
    const rightReady = right.comparable && right.earnedSamples >= DISCOVERY_RUNS ? 1 : 0;
    if (leftReady !== rightReady) return rightReady - leftReady;
  }
  const leftValue = strategySortValue(left, mode);
  const rightValue = strategySortValue(right, mode);
  if (leftValue === null && rightValue !== null) return 1;
  if (leftValue !== null && rightValue === null) return -1;
  if (leftValue !== null && rightValue !== null && leftValue !== rightValue) {
    return rightValue - leftValue;
  }
  const bySamples = strategySortSamples(right, mode) - strategySortSamples(left, mode);
  if (bySamples !== 0) return bySamples;
  return (
    strategyRowName(left).localeCompare(strategyRowName(right)) ||
    left.candidateId.localeCompare(right.candidateId)
  );
}

type StrategyReliability = { category: KaCategory; label: string; detail: string };

/** The reliability marker: comparability first, then how many runs the ranked mean covers. */
function strategyRowReliability(row: EncounterStrategyRow): StrategyReliability {
  if ((row.windows?.length ?? 0) > 1 && row.meanEarned == null) {
    return { category: "warning", label: "separate conditions", detail: "Open this build to see each development and confirmation reading under its own tested conditions." };
  }
  if (!row.comparable) {
    return {
      category: "warning",
      label: "not comparable",
      detail: "the host reports this build's bank is not comparable to the other strategies for this fight",
    };
  }
  if (row.earnedSamples < DISCOVERY_RUNS) {
    return {
      category: "warning",
      label: `thin sample · n=${formatInt(row.earnedSamples)}`,
      detail: `mean earned covers ${formatInt(row.earnedSamples)} run(s), below the ${formatInt(DISCOVERY_RUNS)}-run discovery bank`,
    };
  }
  return {
    category: "success",
    label: `comparable · n=${formatInt(row.earnedSamples)}`,
    detail: `mean earned covers ${formatInt(row.earnedSamples)} comparable run(s)`,
  };
}

/** Seeds of one persisted run, or null when the host recorded none. */
function runSeeds(run: StrategyRun | undefined): SeedPair | null {
  const seeds = run?.seeds;
  if (!Array.isArray(seeds) || seeds.length < 2) return null;
  const math = seeds[0];
  const lib = seeds[1];
  if (typeof math !== "number" || typeof lib !== "number") return null;
  return [math, lib];
}

/** Chests one run earned, reusing the page's single chest rule for whichever field the host stored. */
function runEarnedValue(run: StrategyRun | undefined): number | null {
  const value = run?.earned;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const chest = chestReading(run);
  return chest.known ? chest.count : null;
}

/** Chests one run reached as an opportunity, only where the host scored it. */
function runPotentialValue(run: StrategyRun | undefined): number | null {
  const value = run?.potential;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** One persisted run as the batch aggregator reads it, through the page's own single reading rule. */
function batchReading(run: StrategyRun): BatchRunReading {
  return {
    outcome: runOutcome(run),
    earned: runEarnedValue(run),
    potential: runPotentialValue(run),
    seeds: runSeeds(run),
  };
}

function scenarioEncounterId(scenario: unknown): number | null {
  const value = (scenario as { encounterId?: unknown } | null | undefined)?.encounterId;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Who a frozen scenario brings, read the same way `partyOf` reads a candidate's scenario. */
function scenarioParty(scenario: unknown): { allies: string[]; pets: string[] } {
  const units = (scenario as { ownUnits?: PartyMember[] } | null | undefined)?.ownUnits ?? [];
  const allies: string[] = [];
  const pets: string[] = [];
  for (const unit of units) {
    const name = unit?.name ?? (unit?.monsterId != null ? `Monster ${unit.monsterId}` : "unnamed unit");
    (unit?.human === false ? pets : allies).push(name);
  }
  return { allies, pets };
}

function verdictLabel(verdict: number | null | undefined): string {
  if (verdict === 1) return "win";
  if (verdict === 2) return "loss";
  return verdict == null ? UNKNOWN_METRIC : String(verdict);
}

/** Two targets are the same build only when both identity fields match. */
function sameInvestigationTarget(
  left: InvestigationTarget | null,
  right: InvestigationTarget | null,
): boolean {
  if (!left || !right) return false;
  return (
    (left.candidateId ?? null) === (right.candidateId ?? null) &&
    (left.holderKey ?? null) === (right.holderKey ?? null)
  );
}

/**
 * The one axis-probe control. It is rendered in the Thresholds & ranges card and in the strategy
 * investigation panel, so both contexts offer the exact same axes, opt-in and start action.
 */
function ProbeAxisControl({
  disabled,
  starting,
  axis,
  axis2,
  allowUnestablished,
  onAxis,
  onAxis2,
  onAllowUnestablished,
  onStart,
}: {
  disabled: boolean;
  starting: boolean;
  axis: string;
  axis2: string;
  allowUnestablished: boolean;
  onAxis: (value: string) => void;
  onAxis2: (value: string) => void;
  onAllowUnestablished: (value: boolean) => void;
  onStart: () => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4" data-probe-control>
      <div className="space-y-1">
        <Label className="text-xs">Axis</Label>
        <Select value={axis} onValueChange={onAxis} disabled={disabled}>
          <SelectTrigger className="h-8 w-44 text-xs" data-probe-axis-select>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(allowUnestablished ? [...PROBE_AXES, ...UNESTABLISHED_PROBE_AXES] : [...PROBE_AXES]).map(
              (option) => (
                <SelectItem key={option} value={option}>
                  {axisName(option)} ({option})
                </SelectItem>
              ),
            )}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Second axis</Label>
        <Select value={axis2} onValueChange={onAxis2} disabled={disabled}>
          <SelectTrigger className="h-8 w-44 text-xs" data-probe-axis2-select>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_SECOND_AXIS}>none — single axis</SelectItem>
            {(allowUnestablished ? [...PROBE_AXES, ...UNESTABLISHED_PROBE_AXES] : [...PROBE_AXES]).map(
              (option) => (
                <SelectItem key={option} value={option} disabled={option === axis}>
                  {axisName(option)} ({option})
                </SelectItem>
              ),
            )}
          </SelectContent>
        </Select>
        <p className="text-[10px] text-muted-foreground">
          two axes measure a grid of rungs, not a single ladder
        </p>
      </div>
      <label className="flex items-center gap-2 pb-1 text-xs" data-probe-unestablished>
        <Checkbox
          checked={allowUnestablished}
          disabled={disabled}
          onCheckedChange={(checked) => onAllowUnestablished(checked === true)}
        />
        measure an unestablished input (Intelligence), sent as unestablished
      </label>
      <Button
        type="button"
        onClick={onStart}
        disabled={disabled || (axis2 !== NO_SECOND_AXIS && axis2 === axis)}
        data-action="start-probe"
      >
        <Play className="mr-1 h-3.5 w-3.5" />
        {starting ? "Starting probe…" : "Start probe"}
      </Button>
    </div>
  );
}

/**
 * One bank's aggregate row, rendered straight from the host's flat summary - never re-tallied from
 * the returned rows. Lifetime attempts and retained samples are separate columns, so a bank whose
 * older runs rolled off retention says so instead of presenting the retained rows as the whole bank.
 */
function InvestigationBankRow({
  label,
  dataBank,
  summary,
}: {
  label: string;
  dataBank: string;
  summary: BankSummary | null | undefined;
}) {
  if (!summary) {
    return (
      <TableRow data-investigation-bank={dataBank}>
        <TableCell className="text-xs font-medium">{label}</TableCell>
        <TableCell colSpan={8} className="text-xs text-muted-foreground">
          The host published no summary for this bank.
        </TableCell>
      </TableRow>
    );
  }
  const rolledOff = summary.attempts > summary.samples;
  return (
    <TableRow data-investigation-bank={dataBank} data-bank-rolled-off={rolledOff ? "true" : "false"}>
      <TableCell className="text-xs font-medium">{label}</TableCell>
      <TableCell className="text-xs tabular-nums" data-bank-attempts>
        {formatInt(summary.attempts)}
      </TableCell>
      <TableCell className="text-xs tabular-nums" data-bank-retained>
        {formatInt(summary.samples)}
        {rolledOff ? (
          <span className="ml-1 text-[10px] text-muted-foreground">
            ({formatInt(summary.attempts - summary.samples)} rolled off)
          </span>
        ) : null}
      </TableCell>
      <TableCell className="text-xs tabular-nums">{formatInt(summary.wins)}</TableCell>
      <TableCell className="text-xs tabular-nums">{formatInt(summary.losses)}</TableCell>
      <TableCell className="text-xs tabular-nums">{formatInt(summary.noVerdict)}</TableCell>
      <TableCell className="text-xs tabular-nums">{formatPercent(summary.winRate)}</TableCell>
      <TableCell
        className="text-xs tabular-nums"
        title="Mean chests earned over recorded resolved outcomes; n is the measured earned sample count"
      >
        {formatMetric(summary.meanEarned)}{" "}
        <span className="text-[10px] text-muted-foreground">n={formatInt(summary.earnedSamples)}</span>
      </TableCell>
      <TableCell
        className="text-xs tabular-nums"
        title="Mean chest opportunity over recorded defeats with a reading; an opportunity, never a yield"
      >
        {formatMetric(summary.meanPotential)}{" "}
        <span className="text-[10px] text-muted-foreground">
          n={formatInt(summary.potentialSamples)}
        </span>
      </TableCell>
    </TableRow>
  );
}

/**
 * One scenario unit's recorded build.
 *
 * The investigation panel must show the exact build that produced a record, so every slot is shown
 * separately: its weapon, each equipment piece, every recorded parameter and every skill with the
 * invocation level the scenario aligned to it. The numbers are the scenario's raw recorded values;
 * the page's "effective stats" table is the runner-prepared view and is a different thing, so the
 * caption says so instead of presenting one as the other. A name the canonical catalog does not
 * resolve keeps its numeric id rather than being invented.
 */
function TeamBuildSlotCard({ slot }: { slot: TeamBuildSlot }) {
  const recordedCombatParameters = slot.parameters.filter(
    (parameter) => ![20, 21, 22].includes(parameter.id),
  );
  const weaponLabel =
    slot.weaponName ?? (slot.weaponId != null ? `Equipment ${slot.weaponId}` : "not recorded");
  return (
    <div className="rounded-md border p-3" data-team-build-slot={slot.slot} data-team-build-kind={slot.kind}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="font-mono text-[10px]">
          {slot.label}
        </Badge>
        <span className="text-xs font-semibold" data-team-build-name>
          {slot.name}
        </span>
        <Badge variant="secondary" className="text-[10px]">
          {slot.kind}
        </Badge>
        <span className="text-[11px] text-muted-foreground">
          weapon <span data-team-build-weapon={slot.weaponId ?? ""}>{weaponLabel}</span>
          {slot.weaponId != null ? <span className="font-mono"> (id {slot.weaponId})</span> : null}
          {slot.weaponName == null && slot.weaponId != null ? " — name not recovered" : ""}
        </span>
      </div>

      {/*
        The combat reading first: only the parameters a fight actually reads (see
        `COMBAT_PARAMETER_KEYS` in the shared helper). Gathering, MOV and Love are not here; they
        stay in the raw parameters below, where the provenance lives. Values are the scenario's
        recorded raw values - never a runner-prepared effective value - and an unbounded parameter
        shows its value alone rather than the 2147483647 "no ceiling" sentinel.
      */}
      <div data-team-build-combat>
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Combat stats · recorded raw
        </p>
        {slotParameterView(slot.parameters).combat.length === 0 ? (
          <p className="mt-1 text-[11px] text-muted-foreground">no combat parameter recorded</p>
        ) : (
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {slotParameterView(slot.parameters).combat.map((parameter) => {
              const reading = parameterReading(parameter);
              return (
                <li
                  key={parameter.id}
                  className="rounded border bg-muted/30 px-2 py-1 text-[11px]"
                  data-team-build-combat-stat={parameter.id}
                  title={
                    reading.unbounded
                      ? "Recorded raw value. This parameter has no native ceiling, so no maximum is shown."
                      : "Recorded raw value / recorded maximum. Equipment and runner-prepared effects are not included."
                  }
                >
                  <span className="text-muted-foreground">{axisName(PARAMETER_STAT_KEYS[parameter.id], parameter.label)}</span>{" "}
                  <span className="font-mono tabular-nums">{reading.text}</span>
                  {parameter.id === STAT_PARAMETER_IDS.int ? (
                    <span className="ml-1 text-[10px] text-muted-foreground">magic damage</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div data-team-build-equipment>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Equipment
          </p>
          {slot.equipment.length === 0 ? (
            <p className="mt-1 text-[11px] text-muted-foreground">no equipment recorded</p>
          ) : (
            <ul className="mt-1 space-y-0.5 text-[11px]">
              {slot.equipment.map((piece, index) => (
                <li
                  key={`${piece.id ?? "unknown"}-${index}`}
                  className="flex flex-wrap items-baseline gap-1"
                  data-team-build-equipment-id={piece.id ?? "unknown"}
                >
                  <span className="font-medium">
                    {piece.name ?? (piece.id != null ? `Equipment ${piece.id}` : "unrecorded piece")}
                  </span>
                  <span className="font-mono text-muted-foreground">
                    id {piece.id ?? "?"} · level {piece.level ?? "?"} · affinity {piece.affinity ?? "?"}
                  </span>
                  {piece.name == null && piece.id != null ? (
                    <span className="text-muted-foreground">(name not recovered)</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>

        <details className="text-[11px]" data-team-build-advanced>
          <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Recorded combat parameter details ({recordedCombatParameters.length})
          </summary>
          <p className="mt-1 text-[10px] text-muted-foreground" data-team-build-advanced-note>
            One build's raw stat snapshot, not measured working limits. Gathering, MOV and Love are
            excluded because combat does not use them.
          </p>
          <div data-team-build-parameters>
          {recordedCombatParameters.length === 0 ? (
            <p className="mt-1 text-[11px] text-muted-foreground">no parameters recorded</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="h-7 text-[10px]">Stat</TableHead>
                  <TableHead className="h-7 text-right text-[10px]">raw (value/max)</TableHead>
                  <TableHead className="h-7 text-right text-[10px]">extra (value/max)</TableHead>
                  <TableHead className="h-7 text-right text-[10px]">training</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recordedCombatParameters.map((parameter) => (
                  <TableRow key={parameter.id} data-team-build-parameter={parameter.id}>
                    <TableCell className="py-1 text-[11px]">
                      {axisName(PARAMETER_STAT_KEYS[parameter.id], parameter.label)}{" "}
                      <span className="font-mono text-muted-foreground">
                        (id {parameter.id}
                        {parameter.source === "native"
                          ? ""
                          : parameter.source === "stat-key"
                            ? ", site key"
                            : ", name not recovered"}
                        )
                      </span>
                    </TableCell>
                    <TableCell
                      className="py-1 text-right font-mono text-[11px] tabular-nums"
                      data-team-build-parameter-raw
                      title={
                        parameterReading(parameter).unbounded
                          ? "Recorded raw value. This parameter records no ceiling, so no maximum is shown."
                          : "Recorded raw value / recorded maximum."
                      }
                    >
                      {parameterReading(parameter).text}
                    </TableCell>
                    <TableCell className="py-1 text-right font-mono text-[11px] tabular-nums">
                      {formatNumber(parameter.extraValue)}/{formatNumber(parameter.extraMax)}
                    </TableCell>
                    <TableCell className="py-1 text-right font-mono text-[11px] tabular-nums">
                      {formatNumber(parameter.trainingLevel)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          </div>
        </details>
      </div>

      <div className="mt-3" data-team-build-skills>
        <p
          className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
          data-team-build-skills-label
        >
          Skills · recorded loadout, unchanged by stat fine-tuning — {slot.skillsRecorded} recorded /{" "}
          {slot.invocationLevelsRecorded} invocation levels
        </p>
        {slot.skills.length === 0 ? (
          <p className="mt-1 text-[11px] text-muted-foreground">no skills recorded</p>
        ) : (
          <ul className="mt-1 space-y-0.5 text-[11px]">
            {slot.skills.map((skill) => (
              <li
                key={skill.slot}
                className="flex flex-wrap items-center gap-1"
                data-team-build-skill-slot={skill.slot}
                data-team-build-skill-id={skill.id ?? ""}
                data-team-build-invocation={skill.invocationLevel ?? ""}
                data-team-build-skill-paired={skill.paired ? "true" : "false"}
              >
                <span className="font-mono text-muted-foreground">{skill.slot}.</span>
                <span className="font-mono">#{skill.id ?? "?"}</span>
                {getSkillIcon(skill.name) ? (
                  <img src={getSkillIcon(skill.name)} alt="" aria-hidden="true" className="h-5 w-5 shrink-0 object-contain [image-rendering:pixelated]" loading="lazy" />
                ) : null}
                <span className="font-medium">{skill.name ?? "name not recovered"}</span>
                <span className="text-muted-foreground">
                  invocation{" "}
                  <span className="font-mono">{skill.invocationLevel ?? "?"}</span>
                  {" · "}
                  {skill.triggerLabel}
                </span>
                {skill.paired ? null : <span className="text-amber-600 dark:text-amber-500">unpaired</span>}
              </li>
            ))}
          </ul>
        )}
        {slot.notes.map((note) => (
          <p key={note} className="mt-1 text-[10px] text-amber-600 dark:text-amber-500">
            {note}
          </p>
        ))}
      </div>
    </div>
  );
}

/**
 * The investigation panel's Build section: every own-unit slot of the frozen scenario, in the exact
 * order it recorded. The same section is rendered for a ranked resident candidate and for a pruned
 * record holder, because both resolve to the same frozen scenario. The combat view above summarises
 * the same slots; this is the exact build behind the record.
 */
function TeamBuildSection({ slots }: { slots: TeamBuildSlot[] }) {
  return (
    <div data-investigation-team-build>
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Build — every slot in detail
      </p>
      <p className="mb-2 text-[10px] text-muted-foreground">
        Weapon, equipment, combat stats and each skill's activation setting, per slot of the frozen
        scenario. Values are the scenario's recorded raw values, not runner-prepared effective ones;
        a name the canonical catalog does not resolve keeps its numeric id.
      </p>
      {slots.length === 0 ? (
        <p className="text-xs text-muted-foreground">The frozen scenario recorded no own units.</p>
      ) : (
        <div className="space-y-2" data-team-build-slots={slots.length}>
          {slots.map((slot) => (
            <details key={slot.slot} className="rounded-lg border p-3"><summary className="cursor-pointer text-sm font-medium">#{slot.slot} · {slot.name} · exact recorded values</summary><div className="mt-3"><TeamBuildSlotCard slot={slot} /></div></details>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The workspace's concise combat team overview: one line per own-unit slot, and nothing a fight does
 * not read. It is built from the same `teamBuildSlots` data as the detailed Build section below, so
 * it is a summary and not a second rendering of the build. Its lines deliberately carry only the
 * combat parameters (`COMBAT_PARAMETER_KEYS`): Gathering, MOV and Love are not combat inputs, and
 * Intelligence is, so INT appears here like any other combat stat.
 */
function TeamCombatOverview({ slots }: { slots: TeamBuildSlot[] }) {
  if (slots.length === 0) {
    return (
      <p className="text-xs text-muted-foreground" data-investigation-combat-overview>
        The frozen scenario recorded no own units, so there is no combat team to summarise.
      </p>
    );
  }
  return (
    <div className="space-y-1.5" data-investigation-combat-overview>
      {slots.map((slot) => {
        const combat = slotParameterView(slot.parameters).combat;
        const weapon =
          slot.weaponName ?? (slot.weaponId != null ? `Equipment ${slot.weaponId}` : "no weapon recorded");
        return (
          <div
            key={slot.slot}
            className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-md border px-2.5 py-1.5 text-[11px]"
            data-combat-overview-slot={slot.slot}
          >
            <Badge variant="outline" className="font-mono text-[10px]">
              {slot.label}
            </Badge>
            <span className="font-medium">{slot.name}</span>
            <span className="text-muted-foreground">{slot.kind}</span>
            <span className="text-muted-foreground">
              weapon <span className="text-foreground">{weapon}</span>
            </span>
            <span className="text-muted-foreground">
              {formatInt(slot.equipment.length)} equipment · {formatInt(slot.skills.length)} skills
            </span>
            <span className="font-mono tabular-nums text-muted-foreground">
              {combat.length
                ? combat.map((parameter) => `${axisName(PARAMETER_STAT_KEYS[parameter.id], parameter.label)} ${parameterReading(parameter).text}`).join(" · ")
                : "no combat stat recorded"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * One click-through reading in the Encounter overview's table. It is a real button, so it is keyboard
 * reachable and shows a clear hover/focus state, and its click stops propagation so the difficulty
 * row's own focus action never fires as a second action. A reading the host has not measured renders
 * as unknown, is disabled and is not clickable - the cell never opens a build it does not have to
 * hide a missing reading.
 *
 * The shared column header above the cell already names the reading, so the cell carries only the
 * large number and, for an average, its sample count as secondary text - the label is never repeated
 * inside a row. `label` still names the reading to assistive tech through the button's own
 * `aria-label`, and the long explanation lives in `tooltip`, exposed as the button's native title and
 * as an `aria-describedby` description. `highlighted` adds the short-lived "this reading just
 * improved" glow.
 */
function EncounterMetricCell({
  metric,
  label,
  display,
  note,
  target,
  action,
  tooltip,
  highlighted = false,
  helpId,
  onOpen,
}: {
  metric: string;
  label: string;
  display: string;
  note?: string | null;
  target?: string | null;
  action?: string;
  tooltip: string;
  highlighted?: boolean;
  helpId?: string;
  onOpen: () => void;
}) {
  const known = display !== "—";
  return (
    <button
      type="button"
      disabled={!known}
      data-encounter-metric={metric}
      data-encounter-metric-known={known ? "true" : "false"}
      data-encounter-metric-improved={highlighted ? "true" : "false"}
      data-encounter-metric-target={known ? target ?? undefined : undefined}
      data-action={action}
      title={tooltip}
      aria-describedby={helpId}
      aria-label={`${label}: ${display}${note ? ` (${note})` : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        if (known) onOpen();
      }}
      className={cn(
        /*
         * A right-aligned stack: the number is the only thing read at a glance, so it is the largest
         * text in the row, with the average's sample count as a smaller second line. The header above
         * carries the reading's name, so no label is repeated here.
         */
        "flex w-full min-w-0 flex-col items-end rounded border border-transparent px-1 py-0.5 text-right",
        known
          ? "transition-colors hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
          : "cursor-default opacity-80",
        highlighted && "ka-metric-improved",
      )}
    >
      <span
        className={cn(
          "font-mono text-sm font-semibold leading-tight tabular-nums",
          known ? "text-foreground" : "text-muted-foreground",
        )}
        data-encounter-metric-value
      >
        {display}
      </span>
      {note ? (
        <span className="font-sans text-[9px] leading-tight text-muted-foreground" data-encounter-metric-n>
          {note}
        </span>
      ) : null}
      {helpId ? (
        <span className="sr-only" id={helpId}>
          {tooltip}
        </span>
      ) : null}
    </button>
  );
}

type NotableRun = { role: string; title: string; run: StrategyRun };

/**
 * A small curated set of the runs already recorded for this build: the best award, a loss, the
 * lowest award and the most recent run. The full stored/trial ledger stays one click away in the Run
 * history section, so the primary view never dumps 25+ rows; the 125 run a reader came here for is
 * the "Best earned" card.
 */
function notableRuns(runs: readonly StrategyRun[]): NotableRun[] {
  const resolved = runs.filter((run) => {
    const outcome = runOutcome(run);
    return outcome === "win" || outcome === "loss";
  });
  const byEarned = [...resolved].sort(
    (left, right) => (runEarnedValue(right) ?? -1) - (runEarnedValue(left) ?? -1),
  );
  const picked: NotableRun[] = [];
  const chosen = new Set<StrategyRun>();
  const take = (role: string, title: string, run: StrategyRun | undefined) => {
    if (!run || chosen.has(run)) return;
    chosen.add(run);
    picked.push({ role, title, run });
  };
  take("best", "Best earned", byEarned[0]);
  take(
    "loss",
    "A loss",
    resolved.find((run) => runOutcome(run) === "loss"),
  );
  take("low", "Lowest earned", byEarned[byEarned.length - 1]);
  take("latest", "Latest recorded", runs[runs.length - 1]);
  return picked;
}

function NotableRuns({
  runs,
  canReplay,
  busy,
  onReplay,
}: {
  runs: readonly StrategyRun[];
  canReplay: boolean;
  busy: boolean;
  onReplay: (run: StrategyRun) => void;
}) {
  const notable = notableRuns(runs);
  if (notable.length === 0) {
    return (
      <p className="text-xs text-muted-foreground" data-investigation-notable-empty>
        No resolved run is recorded for this build yet, so there is nothing to watch here.
      </p>
    );
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" data-investigation-notable>
      {notable.map((entry) => {
        const outcome = runOutcome(entry.run);
        const seeds = runSeeds(entry.run);
        const replayable = canReplay && !busy && Boolean(seeds) && Boolean(entry.run.digest);
        return (
          <div
            key={entry.role}
            className="flex flex-col gap-1.5 rounded-md border px-2.5 py-2"
            data-notable-run={entry.role}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {entry.title}
              </span>
              <ToneBadge category={outcomeCategory(outcome)}>{RUN_OUTCOME_LABEL[outcome]}</ToneBadge>
            </div>
            <span className="font-mono text-sm tabular-nums">
              {formatMetric(runEarnedValue(entry.run), 0)} <span className="text-[10px] text-muted-foreground">chests earned</span>
            </span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {seeds ? `[${seeds.join(", ")}]` : "no seed pair recorded"}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-auto"
              disabled={!replayable}
              onClick={() => onReplay(entry.run)}
              data-action="notable-run-replay"
              data-notable-role={entry.role}
              title={
                replayable
                  ? "Replay this exact recorded run on its own seed pair"
                  : busy
                    ? "A simulation is running; wait for it to finish before replaying a run"
                    : "This row cannot be replayed (no replay API, seed pair or digest)"
              }
            >
              <Swords className="mr-1 h-3.5 w-3.5" /> Watch
            </Button>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The result of the batch just run, sitting directly under the simulate buttons instead of in the
 * ledger. Every count and mean is the host's own aggregate over this one requested batch (merged
 * across the chunk calls it was split into); best/low/spread are read off the batch's own rows. A
 * comparison with the stored bank is offered only when the two share one sample definition.
 */
function LatestTestCard({
  batch,
  runMoreCount,
  busy,
  canSimulateVisual,
  canInspect,
  onRunMore,
  onSimulateVisual,
  onInspect,
}: {
  batch: TrialBatchSummary;
  runMoreCount: number;
  busy: boolean;
  canSimulateVisual: boolean;
  canInspect: boolean;
  onRunMore: () => void;
  onSimulateVisual: () => void;
  onInspect: () => void;
}) {
  const comparison = batch.comparison;
  const delta =
    comparison.delta == null ? "—" : `${comparison.delta >= 0 ? "+" : ""}${formatMetric(comparison.delta)}`;
  return (
    <Card className="border-primary/40 bg-primary/5" data-latest-test={batch.requested}>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <FlaskConical className="h-4 w-4" /> Latest test: {formatInt(batch.requested)} fights
          </CardTitle>
          <span className="text-[10px] text-muted-foreground" data-latest-test-scope>
            this batch only · {formatInt(batch.reported)} of {formatInt(batch.requested)} fights reported
            back
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile label="Wins" value={formatInt(batch.wins)} hint={`of ${formatInt(batch.reported)} fights`} />
          <StatTile
            label="Losses"
            value={formatInt(batch.losses)}
            hint="the loss gate released no chest"
          />
          <StatTile
            label="No verdict"
            value={formatInt(batch.noVerdict)}
            hint={
              batch.noVerdict
                ? "reached the simulation safety limit; neither a win nor a loss, and not a loss"
                : "every fight resolved"
            }
          />
          <StatTile
            label="Mean earned"
            value={formatMetric(batch.meanEarned)}
            hint={`n=${formatInt(batch.earnedSamples)} resolved run(s) · defeats count as 0`}
          />
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground" data-latest-test-spread>
          <span>
            best <span className="font-mono text-foreground">{formatMetric(batch.bestEarned, 0)}</span>
          </span>
          <span>
            low <span className="font-mono text-foreground">{formatMetric(batch.lowEarned, 0)}</span>
          </span>
          <span>
            spread <span className="font-mono text-foreground">{formatMetric(batch.spreadEarned, 0)}</span>
          </span>
          {batch.meanPotential != null ? (
            <span>
              mean potential · loss-only{" "}
              <span className="font-mono text-foreground">{formatMetric(batch.meanPotential)}</span> (n=
              {formatInt(batch.potentialSamples)})
            </span>
          ) : null}
        </div>
        <p className="text-[11px]" data-latest-test-comparison={comparison.state}>
          {comparison.state === "available" ? (
            <>
              vs the stored bank: {formatMetric(comparison.storedMean)} chests/run over n=
              {formatInt(comparison.storedSamples)} · this batch is{" "}
              <span className="font-mono">{delta}</span> chests/run.{" "}
            </>
          ) : (
            <>Not compared with the stored bank. </>
          )}
          {comparison.note}
        </p>
        <p className="text-[10px] text-muted-foreground" data-latest-test-caveat>
          {formatInt(batch.requested)} fight(s) is a small sample. It describes what happened in this
          batch, not a proven win rate or a statistically established mean; run more before treating
          the difference as real. A zero on a loss is the native gate releasing no chest, and an
          unknown mean means the host measured no earned reading for the retained runs.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={onRunMore}
            data-action="latest-run-more"
          >
            <Play className="mr-1 h-3.5 w-3.5" /> Run {formatInt(runMoreCount)} more
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy || !canSimulateVisual}
            onClick={onSimulateVisual}
            data-action="latest-simulate-visual"
          >
            <Eye className="mr-1 h-3.5 w-3.5" /> Simulate 1 visually
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy || !canInspect}
            onClick={onInspect}
            data-action="latest-inspect-best"
            title="Replay the best-earning run of this batch on its own seed pair"
          >
            <Swords className="mr-1 h-3.5 w-3.5" /> Inspect the best run
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
/**
 * The seeds/outcome/earned/potential table shared by stored runs and cumulative fresh trials.
 *
 * A bank can retain hundreds of rows, so only `INVESTIGATION_RUN_PAGE` render at once and the rest
 * stay one "Show more" (or "Show all") click away: every retained run and its replay stays reachable.
 * A row's replay is offered only when the host exposes `replay_strategy_run` and the run kept both a
 * seed pair and a digest to verify against, so a disabled button always states a real reason.
 */
function InvestigationRunTable({
  runs,
  canReplay,
  busy = false,
  onReplay,
}: {
  runs: StrategyRun[];
  canReplay: boolean;
  busy?: boolean;
  onReplay: (run: StrategyRun) => void;
}) {
  const [visible, setVisible] = useState(INVESTIGATION_RUN_PAGE);
  if (runs.length === 0) {
    return (
      <p className="text-xs text-muted-foreground" data-investigation-runs-empty>
        No run is persisted for this bank yet.
      </p>
    );
  }
  const limit = Math.max(INVESTIGATION_RUN_PAGE, visible);
  const shown = runs.slice(0, limit);
  const hidden = runs.length - shown.length;
  const paginated = runs.length > INVESTIGATION_RUN_PAGE;
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <Table data-investigation-runs>
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>
              <TableHead>Seeds [math, lib]</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead>Earned</TableHead>
              <TableHead>Potential · loss-only</TableHead>
              <TableHead>Retained</TableHead>
              <TableHead>Run digest</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((run, index) => {
              const outcome = runOutcome(run);
              const seeds = runSeeds(run);
              const retained = retainedRunValue(run);
              const replayable = canReplay && !busy && Boolean(seeds) && Boolean(run.digest);
              const reason = busy
                ? "A simulation is running; wait for it to finish before replaying a run"
                : !canReplay
                  ? "This host build does not expose replay_strategy_run, so a recorded run cannot be re-verified"
                  : !seeds
                    ? "This run stored no seed pair to replay"
                    : !run.digest
                      ? "This run stored no digest, so the replay could not be verified"
                      : "Replay this exact recorded run on its own seed pair";
              return (
                <TableRow
                  key={`${run.phase ?? "run"}-${run.ordinal ?? index}-${run.digest ?? index}`}
                  data-investigation-run={index}
                  data-run-outcome={outcome}
                >
                  <TableCell className="text-xs tabular-nums">{index + 1}</TableCell>
                  <TableCell className="font-mono text-[11px]">
                    {seeds ? `[${seeds.join(", ")}]` : "—"}
                  </TableCell>
                  <TableCell className="text-xs">
                    <ToneBadge category={outcomeCategory(outcome)}>{RUN_OUTCOME_LABEL[outcome]}</ToneBadge>
                  </TableCell>
                  <TableCell className="text-xs tabular-nums">{formatMetric(runEarnedValue(run), 0)}</TableCell>
                  <TableCell className="text-xs tabular-nums">
                    {outcome === "loss" ? formatMetric(runPotentialValue(run), 0) : "—"}
                  </TableCell>
                  <TableCell className="text-xs tabular-nums">
                    {retained === null ? "unverified" : formatInt(retained)}
                  </TableCell>
                  <TableCell className="font-mono text-[10px] text-muted-foreground">
                    {run.digest ? run.digest.slice(0, 16) : "—"}
                  </TableCell>
                  <TableCell>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={!replayable}
                      onClick={() => onReplay(run)}
                      data-action="investigation-replay-run"
                      title={reason}
                    >
                      <Swords className="mr-1 h-3.5 w-3.5" /> Replay
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {paginated ? (
        <div className="flex flex-wrap items-center gap-2" data-investigation-runs-more>
          <span className="text-[11px] text-muted-foreground">
            Showing {formatInt(shown.length)} of {formatInt(runs.length)} runs
          </span>
          {hidden > 0 ? (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setVisible(limit + INVESTIGATION_RUN_PAGE)}
                data-action="investigation-show-more"
              >
                Show {formatInt(Math.min(INVESTIGATION_RUN_PAGE, hidden))} more
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setVisible(runs.length)}
                data-action="investigation-show-all"
              >
                Show all {formatInt(runs.length)}
              </Button>
            </>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setVisible(INVESTIGATION_RUN_PAGE)}
              data-action="investigation-show-less"
            >
              Show first {formatInt(INVESTIGATION_RUN_PAGE)}
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Focused search portfolio                                            */
/* ------------------------------------------------------------------ */

/**
 * The four objective lanes, in the order the host defines them, each with a concise label and the one
 * sentence that explains what its number means. A lane compares only within itself: a maximum against
 * maxima, a mean against means, so the labels keep "best" and "mean" apart on purpose.
 */
const PORTFOLIO_LANES: Array<{ objective: PortfolioObjective; label: string; hint: string }> = [
  {
    objective: "highest-earned",
    label: "Best earned",
    hint: "Best converted chest outcome ever observed for this fight (a lifetime maximum).",
  },
  {
    objective: "highest-potential",
    label: "Best potential",
    hint: "Best loss-only chest opportunity ever built for this fight (a lifetime maximum).",
  },
  {
    objective: "average-earned",
    label: "Mean earned",
    hint: "Mean released chests over the resolved validation bank; admitted only above a resolved-sample floor.",
  },
  {
    objective: "average-potential",
    label: "Mean potential",
    hint: "Mean loss-only chest opportunity over resolved defeats.",
  },
];

const PORTFOLIO_LANE_LABEL: Record<PortfolioObjective, string> = {
  "highest-earned": "Best earned",
  "highest-potential": "Best potential",
  "average-earned": "Mean earned",
  "average-potential": "Mean potential",
};

/** A maximum is rendered as a whole count; a mean keeps two decimals, exactly as the host published it. */
function portfolioValueText(objective: PortfolioObjective, value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "unknown";
  return objective.startsWith("highest") ? formatInt(value) : formatNumber(value, 2);
}

/** The one tag a lane member can carry: served now, reserved for a newcomer, or graduated. */
function PortfolioStateTag({ state }: { state: "active" | "challenger" | "stable" }) {
  const label = state === "stable" ? "Stable" : state === "challenger" ? "Challenger" : "Active";
  const hint =
    state === "stable"
      ? "Graduated: a maintenance recheck continues periodically, and a measured improvement reverses this."
      : state === "challenger"
        ? "Holds the reserved challenger slot this pass: a new or improving build that is not in a lane."
        : "Serving an active intensive slot this pass.";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[9px] font-medium leading-none",
        state === "stable" && "border-muted-foreground/40 bg-muted text-muted-foreground",
        state === "challenger" && "border-amber-500/60 bg-amber-500/15 text-amber-700 dark:text-amber-300",
        state === "active" && "border-primary/60 bg-primary/15 text-primary",
      )}
      data-portfolio-state={state}
      title={hint}
    >
      {label}
    </span>
  );
}

/**
 * The focused fight's four-objective search portfolio, browsed one lane at a time so four breadth
 * lists never render at once. It is purely a view of the host's plan: lane membership, the sample
 * count behind each reading, which contenders are active/challenger/stable, and the graduated tier
 * with its reversible maintenance rule. The all-build ranking below is untouched, so this panel never
 * filters it and an overview-metric highlight is never hidden behind it.
 */
function SearchPortfolioPanel({
  plan,
  encounterId,
  encounterName,
  rankedById,
  onOpenBuild,
}: {
  plan: FocusPortfolio;
  encounterId: number;
  encounterName: string;
  rankedById: Map<string, EncounterStrategyRow>;
  onOpenBuild: (candidateId: string, label: string | null) => void;
}) {
  const [view, setView] = useState<PortfolioView>("highest-earned");
  /*
   * The browsed lane resets only when the focused encounter changes - a primitive that a status poll
   * does not move - so a republished plan never yanks the reader back to the first lane.
   */
  useEffect(() => {
    setView("highest-earned");
  }, [encounterId]);

  const laneSize = plan.laneSize ?? 10;
  const activeSlots = plan.activeSlots ?? (plan.active?.length ?? 0);
  const stableIds = useMemo(() => plan.stable ?? [], [plan]);
  const recheckEvery = plan.recheckEvery ?? null;

  const stateById = useMemo(() => {
    const map = new Map<string, "active" | "challenger" | "stable">();
    for (const candidateId of plan.stable ?? []) map.set(candidateId, "stable");
    for (const decision of plan.challenger ?? []) map.set(decision.candidate, "challenger");
    for (const decision of plan.active ?? []) {
      if (decision.tier === "challenger") continue;
      if (!map.has(decision.candidate)) map.set(decision.candidate, "active");
    }
    return map;
  }, [plan]);

  /** The first lane a build holds, so the graduated tier can name the reading that admitted it. */
  const laneMembership = useMemo(() => {
    const map = new Map<string, { objective: PortfolioObjective; member: PortfolioLaneMember }>();
    for (const { objective } of PORTFOLIO_LANES) {
      for (const member of plan.lanes?.[objective] ?? []) {
        if (!map.has(member.candidate)) map.set(member.candidate, { objective, member });
      }
    }
    return map;
  }, [plan]);

  const maintenanceById = useMemo(() => {
    const map = new Map<string, PortfolioDecision>();
    for (const decision of plan.maintenance ?? []) map.set(decision.candidate, decision);
    return map;
  }, [plan]);

  const objective = view === "stable" ? null : view;
  const members = objective ? plan.lanes?.[objective] ?? [] : [];
  const filled = members.length;
  const gap = objective
    ? plan.gaps?.[objective] ?? Math.max(0, laneSize - filled)
    : 0;

  return (
    <section
      className="rounded-md border bg-muted/20 p-3"
      data-strategy-portfolio
      data-portfolio-encounter={encounterId}
      aria-label="Focused search portfolio"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
          <Crosshair className="h-3.5 w-3.5" /> Search portfolio
        </span>
        <Badge variant="outline" className="font-normal" data-portfolio-focus>{encounterName}</Badge>
        <Badge
          variant="outline"
          className="font-normal tabular-nums"
          title="Scheduler pass this plan belongs to."
        >
          pass {formatInt(plan.passNumber ?? 0)}
        </Badge>
        <Badge
          variant="outline"
          className="font-normal tabular-nums"
          title="Intensive slots served this pass, out of the bounded active set."
        >
          active {formatInt(plan.active?.length ?? 0)}/{formatInt(activeSlots)}
        </Badge>
        <Badge
          variant="outline"
          className="font-normal tabular-nums"
          title="The reserved challenger slot is held for a new or improving build; it is not drawn from a lane."
        >
          challenger {formatInt((plan.challenger ?? []).length)}/{formatInt(plan.challengerSlots ?? 1)}
        </Badge>
        <Badge
          variant="outline"
          className="font-normal tabular-nums"
          title="Graduated builds in the maintenance tier; rechecks continue and graduation is reversible."
        >
          stable {formatInt(stableIds.length)}
        </Badge>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Four objective lanes share this fight's intensive capacity. Each lane is a breadth bound of
        eligible builds, so a lane leader is the best on that lane's own measure, not a global ranking.
      </p>

      <div className="mt-2 flex flex-wrap gap-1" data-portfolio-lanes>
        {PORTFOLIO_LANES.map(({ objective: laneKey, label, hint }) => {
          const count = plan.laneSizes?.[laneKey] ?? (plan.lanes?.[laneKey]?.length ?? 0);
          const selected = view === laneKey;
          return (
            <Button
              key={laneKey}
              type="button"
              size="sm"
              variant={selected ? "default" : "outline"}
              className="h-7 px-2 text-[11px]"
              onClick={() => setView(laneKey)}
              data-portfolio-lane={laneKey}
              data-portfolio-lane-count={count}
              aria-pressed={selected}
              title={`${hint} ${count} of ${laneSize} slots hold a build that clears this rule.`}
            >
              {label}
              <span className="ml-1 tabular-nums opacity-70">
                {count}/{laneSize}
              </span>
            </Button>
          );
        })}
        <Button
          type="button"
          size="sm"
          variant={view === "stable" ? "default" : "outline"}
          className="h-7 px-2 text-[11px]"
          onClick={() => setView("stable")}
          data-portfolio-lane="stable"
          data-portfolio-lane-count={stableIds.length}
          aria-pressed={view === "stable"}
          title="Builds that passed deep testing and flattened, now in the reversible maintenance tier."
        >
          Stable / established
          <span className="ml-1 tabular-nums opacity-70">{formatInt(stableIds.length)}</span>
        </Button>
      </div>

      <div className="mt-2" data-portfolio-body data-portfolio-view={view}>
        {objective ? (
          <>
            <div className="overflow-x-auto rounded-md border bg-background/60">
              <Table data-portfolio-lane-table={objective}>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8">#</TableHead>
                    <TableHead>Build</TableHead>
                    <TableHead className="text-right">Reading</TableHead>
                    <TableHead className="text-right">Samples</TableHead>
                    <TableHead>State</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((member, index) => {
                    const state = stateById.get(member.candidate) ?? null;
                    return (
                      <TableRow key={member.candidate} data-portfolio-member={member.candidate}>
                        <TableCell className="text-xs tabular-nums">{index + 1}</TableCell>
                        <TableCell>
                          <button
                            type="button"
                            onClick={() => onOpenBuild(member.candidate, member.label ?? null)}
                            data-action="open-portfolio-build"
                            data-portfolio-build={member.candidate}
                            title={`Open the Selected strategy workspace for ${member.label ?? member.candidate}`}
                            aria-label={`Open the Selected strategy workspace for ${member.label ?? member.candidate}`}
                            className="block w-full min-w-0 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                          >
                            <span className="block text-xs font-medium text-foreground underline-offset-2 hover:underline">
                              {member.label ?? shortId(member.candidate)}
                            </span>
                            <span className="block font-mono text-[10px] text-muted-foreground">
                              {member.source ?? "unknown"} · {shortId(member.candidate)}
                            </span>
                          </button>
                        </TableCell>
                        <TableCell
                          className="text-right text-xs tabular-nums"
                          title={
                            member.comparable
                              ? "Measured on the shared validation bank; comparable."
                              : "Screening reading, not yet the shared validation bank."
                          }
                        >
                          {portfolioValueText(objective, member.value)}
                        </TableCell>
                        <TableCell
                          className="text-right text-xs tabular-nums"
                          title="Observations behind this lane's reading."
                        >
                          {formatInt(member.samples ?? 0)}
                        </TableCell>
                        <TableCell>
                          {state ? (
                            <PortfolioStateTag state={state} />
                          ) : (
                            <span
                              className="text-[10px] text-muted-foreground"
                              title="Eligible for this lane and queued; not one of this pass's slots."
                            >
                              in lane
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {members.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-xs text-muted-foreground" data-portfolio-lane-empty>
                        No build clears this objective's evidence rule for this fight right now.
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </div>
            <p className="mt-1 text-[10px] text-muted-foreground" data-portfolio-gap={gap}>
              {gap > 0
                ? `${formatInt(filled)} of ${formatInt(laneSize)} lane slots hold a build that clears this rule; the other ${formatInt(
                    gap,
                  )} are not padded and come from the reserved challenger pool.`
                : `${formatInt(laneSize)} of ${formatInt(laneSize)} lane slots hold eligible builds. Membership is a breadth bound, not a global ranking.`}
            </p>
            {(plan.challenger ?? []).length ? (
              <p className="mt-1 text-[10px] text-muted-foreground" data-portfolio-challenger>
                Reserved challenger:{" "}
                {(plan.challenger ?? [])
                  .map(
                    (decision) =>
                      laneMembership.get(decision.candidate)?.member.label ??
                      rankedById.get(decision.candidate)?.label ??
                      shortId(decision.candidate),
                  )
                  .join(", ")}{" "}
                — a new or improving build, held even when every lane is full.
              </p>
            ) : null}
          </>
        ) : (
          <>
            <div className="overflow-x-auto rounded-md border bg-background/60">
              <Table data-portfolio-stable>
                <TableHeader>
                  <TableRow>
                    <TableHead>Build</TableHead>
                    <TableHead className="text-right">Admitting lane reading</TableHead>
                    <TableHead className="text-right">Mean earned</TableHead>
                    <TableHead className="text-right">Mean potential</TableHead>
                    <TableHead>Maintenance</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stableIds.map((candidateId) => {
                    const membership = laneMembership.get(candidateId) ?? null;
                    const ranked = rankedById.get(candidateId) ?? null;
                    const recheck = maintenanceById.get(candidateId) ?? null;
                    const name = membership?.member.label ?? ranked?.label ?? shortId(candidateId);
                    return (
                      <TableRow key={candidateId} data-portfolio-stable-row={candidateId}>
                        <TableCell>
                          <button
                            type="button"
                            onClick={() => onOpenBuild(candidateId, name)}
                            data-action="open-portfolio-build"
                            data-portfolio-build={candidateId}
                            title={`Open the Selected strategy workspace for ${name}`}
                            aria-label={`Open the Selected strategy workspace for ${name}`}
                            className="block w-full min-w-0 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                          >
                            <span className="block text-xs font-medium text-foreground underline-offset-2 hover:underline">
                              {name}
                            </span>
                            <span className="block font-mono text-[10px] text-muted-foreground">
                              {membership?.member.source ?? ranked?.source ?? "unknown"} · {shortId(candidateId)}
                            </span>
                          </button>
                        </TableCell>
                        <TableCell className="text-right text-xs tabular-nums">
                          {membership ? (
                            <span title="The lane reading that admitted this build.">
                              {PORTFOLIO_LANE_LABEL[membership.objective]}{" "}
                              {portfolioValueText(membership.objective, membership.member.value)} · n=
                              {formatInt(membership.member.samples ?? 0)}
                            </span>
                          ) : (
                            <span className="text-[10px] text-muted-foreground" title="Not in the current top ten of any lane.">
                              not in a current lane
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="text-right text-xs tabular-nums">
                          {ranked ? (
                            <span title="Mean chests earned over the build's resolved runs, with its sample count.">
                              {formatNumber(ranked.meanEarned, 2)} · n={formatInt(ranked.earnedSamples)}
                            </span>
                          ) : (
                            <span className="text-[10px] text-muted-foreground" title="Not in this fight's ranked list.">
                              not ranked
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="text-right text-xs tabular-nums">
                          {ranked ? (
                            <span title="Mean chest opportunity over defeats only, with its sample count.">
                              {formatNumber(ranked.meanPotential, 2)} · n={formatInt(ranked.potentialSamples)}
                            </span>
                          ) : (
                            <span className="text-[10px] text-muted-foreground" title="Not in this fight's ranked list.">
                              not ranked
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="text-[10px] text-muted-foreground">
                          {recheck?.outcome === "reentered" ? (
                            <span data-portfolio-recheck-outcome="reentered">
                              recheck measured an improvement — back in rotation
                            </span>
                          ) : (
                            <span title="At most one graduated build is rechecked per pass, from a ring-fenced maintenance allowance.">
                              {recheckEvery == null ? "periodic recheck" : `recheck every ${formatInt(recheckEvery)} passes`}
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {stableIds.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-xs text-muted-foreground" data-portfolio-stable-empty>
                        No build has graduated yet — nothing has flattened after enough paired comparisons.
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </div>
            <p className="mt-1 text-[10px] text-muted-foreground" data-portfolio-stable-note>
              Stable is a maintenance state, not a claim of global optimality: periodic rechecks continue
              (at most one build per pass, from a ring-fenced allowance), and a recheck that measures an
              improvement reverses graduation and re-enters the build in the rotation.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function StrategyOptimizerDesktopPage() {
  const [workspaceView, setWorkspaceView] = useState<"overview" | "strategies" | "strategy" | "guide" | "settings">("overview");
  const [detailTab, setDetailTab] = useState<"summary" | "team" | "ranges" | "runs" | "test">("summary");
  const trialCancel = useRef(false);
  const replayReturnScroll = useRef(0);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [replayContext, setReplayContext] = useState<{ detail: StrategyDetail; target: InvestigationTarget; programs: FineTuneProgram[] } | null>(null);
  const [api, setApi] = useState<OptimizerApi | null>(() => hostApi());
  const [status, setStatus] = useState<OptimizerStatus | null>(null);
  const [experimentReport, setExperimentReport] = useState<FocusedExperiment | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  /**
   * When the host last answered, and whether that reading has gone stale. The idle warning is derived
   * from these two so it can never be shown from a snapshot the page is no longer able to refresh.
   */
  const [statusAt, setStatusAt] = useState<number | null>(null);
  const [statusStale, setStatusStale] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [exampleLabel, setExampleLabel] = useState<string | null>(null);
  const [workers, setWorkers] = useState(DEFAULT_WORKERS);
  const [duty, setDuty] = useState(DEFAULT_DUTY);
  /** Which fight the strategy list is showing: "all", or one encounter id as a string. */
  const [encounterFilter, setEncounterFilter] = useState<string>("all");
  /**
   * The fight the Encounter Overview has focused, as `"<encounterId>:<defeatCount>"`. It scopes the
   * Strategy families section and the runs-worth-watching cards to one encounter/difficulty, which is
   * what makes "most chests" mean something comparable.
   */
  const [fightKey, setFightKey] = useState<string | null>(null);
  /** The one selected strategy family (its key from `buildStrategyFamilies`). */
  const [familyKey, setFamilyKey] = useState<string | null>(null);
  /** Which strategy families have their member list (and their ranges) open. */
  const [expandedFamilies, setExpandedFamilies] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  /**
   * The MP-recovery allowance being edited, seeded from the host's published setting and re-seeded
   * whenever that setting changes. Controlled rather than uncontrolled because the boxes submit as a
   * whole allowance: a stale box would send back a quantity the host is not using.
   */
  const [mpRecoveryDraft, setMpRecoveryDraft] = useState({
    enabled: false, stock: 0, maxUses: 0, bank: 64, maxChildren: 8,
  });
  const [notice, setNotice] = useState<string | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [replayTitle, setReplayTitle] = useState<string | null>(null);
  const [showReplay, setShowReplay] = useState(false);
  const [replayRecord, setReplayRecord] = useState<GeneratedBattleRecord>();
  /** Probe control for the selected strategy: which axis, or axis pair, to measure next. */
  const [probeAxis, setProbeAxis] = useState<string>(PROBE_AXES[0]);
  const [probeAxis2, setProbeAxis2] = useState<string>(NO_SECOND_AXIS);
  /** Unestablished inputs stay behind this explicit opt-in; the selects only grow when it is on. */
  /** Unestablished inputs stay behind this explicit opt-in; the selects only grow when it is on. */
  const [allowUnestablished, setAllowUnestablished] = useState(false);
  /** The host's ranked strategies for the focused encounter, and how the reader asked to sort them. */
  const [encounterStrategies, setEncounterStrategies] = useState<EncounterStrategiesResult | null>(null);
  const [strategiesBusy, setStrategiesBusy] = useState(false);
  const [strategiesError, setStrategiesError] = useState<string | null>(null);
  /**
   * The fight the loaded `encounterStrategies` belong to. A click on an overview metric resolves
   * its row only against the payload for that exact fight, so a slow reply from the previously
   * focused fight can never be mistaken for this one's ranked list.
   */
  const [strategiesFight, setStrategiesFight] = useState<string | null>(null);
  /**
   * The host's whole-overview average leaders (best measured mean earned / potential per fight),
   * loaded in one call so the Encounter overview never queries per rendered row. `error` is the
   * honest unavailable state, and a null entry inside a leader row is a missed metric, not a zero.
   */
  const [averageLeaders, setAverageLeaders] = useState<OverviewAverageLeadersResult | null>(null);
  const [averageLeadersError, setAverageLeadersError] = useState<string | null>(null);
  /**
   * The library whose average leaders are currently in `averageLeaders`. The overview's improvement
   * highlight waits for this to match the open library, so a slow reply from the previous library is
   * never mistaken for the new library's starting point.
   */
  const [averageLeadersLibrary, setAverageLeadersLibrary] = useState<string | null>(null);
  /** Guards stale average-leader replies so a slow answer never lands on a newer library. */
  const averageLeadersToken = useRef(0);
  const [strategySort, setStrategySort] = useState<StrategySortMode>("earned");
  /**
   * Whether the ranked encounter list includes `source === "probe"` range-test variants. Off by
   * default so the principal strategies are the ones ranked on screen; the toggle is the reader's
   * own route to the probe rungs, and a fight that holds nothing else offers it directly.
   */
  const [showProbeVariants, setShowProbeVariants] = useState(false);
  /**
   * Which Encounter overview reading the reader clicked, and the ranked row it must highlight. The
   * click focuses the fight and points at the exact owning build; it never opens the investigation
   * panel itself, which stays behind the owning build's own name in the ranked list.
   */
  const [overviewMetricFocus, setOverviewMetricFocus] = useState<OverviewMetricFocus | null>(null);
  /** Monotonic so re-clicking the same reading is still a fresh focus that re-scrolls its row. */
  const overviewMetricNonce = useRef(0);
  /** The last `<nonce>:<selector>` pair already scrolled, so a re-sort does not yank the page. */
  const overviewMetricScrollRef = useRef<string | null>(null);
  /**
   * The fine-tuning programs for the build the investigation panel is grouped under, read through
   * the host's dedicated read API rather than derived from the polled snapshot. `fineTuneToken`
   * guards a slow reply so it never lands on a newer parent.
   */
  const [fineTunedPrograms, setFineTunedPrograms] = useState<FineTuneProgram[] | null>(null);
  /** Which parent the fetched `fineTunedPrograms` belong to, so a stale list is never shown. */
  const [fineTuneProgramsParent, setFineTuneProgramsParent] = useState<string | null>(null);
  const [fineTuneError, setFineTuneError] = useState<string | null>(null);
  /** The manual "test another stat / target" command's own in-flight flag and returned refusal. */
  const [fineTuneStarting, setFineTuneStarting] = useState(false);
  const [fineTuneStartError, setFineTuneStartError] = useState<string | null>(null);
  const fineTuneToken = useRef(0);
  /**
   * Which build the investigation panel describes. A record holder is addressed by its key so a
   * pruned candidate stays reachable; a live strategy is addressed by its candidate id.
   */
  const [investigationTarget, setInvestigationTarget] = useState<InvestigationTarget | null>(null);
  const [investigation, setInvestigation] = useState<
    { target: InvestigationTarget; detail: StrategyDetail } | null
  >(null);
  const [investigationBusy, setInvestigationBusy] = useState(false);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  /** Fresh-trial control: the requested total, the completed count and the host's own refusal text. */
  const [trialTotal, setTrialTotal] = useState<number | null>(null);
  const [trialDone, setTrialDone] = useState(0);
  const [trialRunning, setTrialRunning] = useState(false);
  const [trialError, setTrialError] = useState<string | null>(null);
  /**
   * The host's own aggregate over the batch that was just requested, merged across its chunk calls,
   * so the result card reports this batch and not the cumulative trial total.
   */
  const [latestBatch, setLatestBatch] = useState<TrialBatchSummary | null>(null);
  const [savedBatches, setSavedBatches] = useState<{
    scope: string; batches: FocusedExperiment[]; error: string | null; supported: boolean;
  } | null>(null);
  /** The best-earning row of that same batch, so the card can offer to inspect it. */
  const [latestBatchBest, setLatestBatchBest] = useState<StrategyRun | null>(null);
  /**
   * The count box stays a string so it can be cleared while editing; it is parsed and clamped on
   * blur and on submit, never forced back to its minimum mid-keystroke.
   */
  const [trialCountInput, setTrialCountInput] = useState(String(FRESH_TRIAL_DEFAULT));
  /** One-fight visual simulation: its own busy flag, the host's refusal text and its result note. */
  const [visualRunning, setVisualRunning] = useState(false);
  const [visualError, setVisualError] = useState<string | null>(null);
  const [visualNote, setVisualNote] = useState<string | null>(null);
  /**
   * On-demand diagnostic export: the compact control beside Open library, the running job's cached
   * progress and its own error line. The count draft is kept as text so it can be blanked while
   * editing; it is validated on commit, never clamped out from under the cursor.
   */
  const [exportOpen, setExportOpen] = useState(false);
  const [exportCountInput, setExportCountInput] = useState(String(DIAGNOSTIC_EXPORT_DEFAULT));
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportStatus, setExportStatus] = useState<DiagnosticExportStatus | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportStarting, setExportStarting] = useState(false);
  const exportRunning = exportStatus?.state === "running";
  const encounterStrategiesRef = useRef<HTMLDivElement | null>(null);
  const investigationRef = useRef<HTMLDivElement | null>(null);
  /** Guards stale async replies so a slow answer never lands on a newer investigation. */
  const investigationToken = useRef(0);
  const inFlight = useRef(false);
  const [statusTransportPath, setStatusTransportPath] = useState<string | null>(null);
  const [statusTransportReady, setStatusTransportReady] = useState(false);
  const statusTransportFailed = useRef(false);
  useEffect(() => {
    if (api) return;
    const onReady = () => setApi(hostApi());
    window.addEventListener("pywebviewready", onReady);
    return () => window.removeEventListener("pywebviewready", onReady);
  }, [api]);

  useEffect(() => {
    if (!api) {
      setStatusTransportPath(null);
      setStatusTransportReady(false);
      return;
    }
    let active = true;
    statusTransportFailed.current = false;
    setStatusTransportPath(null);
    setStatusTransportReady(false);
    if (!api.status_transport) {
      setStatusTransportReady(true);
      return () => { active = false; };
    }
    let discoveryTimer: number | undefined;
    const discoveryTimeout = new Promise<{path: string}>((_, reject) => {
      discoveryTimer = window.setTimeout(() => reject(new Error("status route discovery timed out")), 5000);
    });
    void Promise.race([api.status_transport(), discoveryTimeout]).then((transport) => {
      if (!transport?.path?.startsWith("/__optimizer_status/")) {
        throw new Error("the host returned an invalid status route");
      }
      if (active) setStatusTransportPath(transport.path);
    }).catch((error) => {
      if (!active) return;
      statusTransportFailed.current = true;
      setStatusError(`Status transport unavailable: ${String(error)}`);
      setStatusStale(true);
    }).finally(() => {
      if (discoveryTimer !== undefined) window.clearTimeout(discoveryTimer);
      if (active) setStatusTransportReady(true);
    });
    return () => {
      active = false;
      if (discoveryTimer !== undefined) window.clearTimeout(discoveryTimer);
    };
  }, [api]);

  const refresh = useCallback(async () => {
    const host = api;
    if (!host || !statusTransportReady || statusTransportFailed.current || inFlight.current) return;
    inFlight.current = true;
    try {
      let next: OptimizerStatus;
      if (host.status_transport) {
        if (!statusTransportPath) throw new Error("the host did not provide its status route");
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 5000);
        let response: Response;
        try {
          response = await fetch(statusTransportPath, { cache: "no-store", signal: controller.signal });
        } finally {
          window.clearTimeout(timeout);
        }
        if (!response.ok) throw new Error(`status route returned HTTP ${response.status}`);
        const envelope = await response.json() as {
          base?: Record<string, unknown>;
          live?: Record<string, unknown>;
        } | null;
        if (!envelope?.base || typeof envelope.base !== "object" || Array.isArray(envelope.base) ||
            !envelope.live || typeof envelope.live !== "object" || Array.isArray(envelope.live)) {
          throw new Error("the host returned an invalid status snapshot");
        }
        next = {
          ...envelope.base,
          ...envelope.live,
          throughput: {
            ...((envelope.base.throughput && typeof envelope.base.throughput === "object")
              ? envelope.base.throughput as Record<string, unknown> : {}),
            ...((envelope.live.throughput && typeof envelope.live.throughput === "object")
              ? envelope.live.throughput as Record<string, unknown> : {}),
          },
          scheduler: {
            ...((envelope.base.scheduler && typeof envelope.base.scheduler === "object")
              ? envelope.base.scheduler as Record<string, unknown> : {}),
            ...((envelope.live.scheduler && typeof envelope.live.scheduler === "object")
              ? envelope.live.scheduler as Record<string, unknown> : {}),
          },
        } as OptimizerStatus;
      } else {
        next = await host.status();
      }
      if (next && typeof next === "object") {
        setStatus(next);
        const transport = next.statusTransport;
        const generatedAt = typeof transport?.generatedAtUnix === "number" &&
          Number.isFinite(transport.generatedAtUnix)
          ? Number(transport?.generatedAtUnix) * 1000
          : Date.now();
        const ageMs = Math.max(0, Date.now() - generatedAt);
        setStatusError(transport?.lastError
          ? `The latest background status refresh failed: ${transport.lastError}`
          : null);
        setStatusAt(generatedAt);
        setStatusStale(ageMs >= IDLE_WARNING_STALE_MS);
      } else {
        setStatusError("the host returned an empty status");
        setStatusStale(true);
      }
    } catch (error) {
      setStatusError(String(error));
      setStatusStale(true);
    } finally {
      inFlight.current = false;
    }
  }, [api, statusTransportPath, statusTransportReady]);

  useEffect(() => {
    if (!api || !statusTransportReady) return;
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [api, refresh, statusTransportReady]);

  /**
   * A reply that stops arriving is a stale reading, not a live one. Each fresh status restarts this
   * expiry, so the idle warning cannot outlive the host's last answer - no reply, no warning.
   */
  useEffect(() => {
    if (statusAt == null) return;
    const remaining = Math.max(0, IDLE_WARNING_STALE_MS - (Date.now() - statusAt));
    const timer = window.setTimeout(() => setStatusStale(true), remaining);
    return () => window.clearTimeout(timer);
  }, [statusAt]);

  const candidates = useMemo(() => status?.candidates ?? [], [status]);
  const archive = useMemo(() => status?.archive ?? [], [status]);
  /** The five encounter cards and the host's own per-difficulty aggregates. */
  const campaigns = useMemo(() => status?.campaigns ?? [], [status]);
  const encounterStats = status?.encounterStats ?? {};
  /**
   * The fights this library is missing. The catalogue lists every encounter the engine can host;
   * `status.libraryEncounters` says which ones this particular library actually carries. A library
   * narrowed to one encounter (an experiment, or one imported before the catalogue was expanded) is
   * legal and stays exactly as it is until the user asks for the rest - nothing here mutates it.
   */
  const missingEncounters = useMemo(() => {
    if (!campaigns.length) return [] as number[];
    const present = new Set(status?.libraryEncounters ?? []);
    const wanted = campaigns.flatMap((campaign) =>
      campaign.difficulties.map((difficulty) => difficulty.encounterId),
    );
    return [...new Set(wanted)].filter((encounterId) => !present.has(encounterId)).sort((a, b) => a - b);
  }, [campaigns, status]);
  /** How many encounters the catalogue itself offers, so the action can state "n of N". */
  const campaignsEncounterCount = useMemo(
    () =>
      new Set(campaigns.flatMap((campaign) =>
        campaign.difficulties.map((difficulty) => difficulty.encounterId))).size,
    [campaigns],
  );
  /** Encounter-wide lifetime record holders, keyed `"<metric>:<encounterId>:<defeatCount>"`. */
  const recordHolders = status?.recordHolders ?? {};
  /**
   * A record holder is only replayable when it carries the frozen replay data - the scenario travels
   * with the record precisely so it survives the pruning of the candidate that set it. A holder
   * written before that existed has no `digest`/seeds, so the button that replays it is disabled
   * instead of letting the click fail in the host. The key names the encounter/difficulty and the
   * metric only: these controls deliberately do not depend on the selected Strategy Family.
   */
  const replayableHolder = useCallback(
    (key: string) => {
      const holder = recordHolders[key];
      if (!holder) return null;
      return holder.digest && Number.isInteger(holder.mathSeed) && Number.isInteger(holder.libSeed)
        ? holder
        : null;
    },
    [recordHolders],
  );
  /**
   * Load every average leader in one call for the whole overview - never one query per rendered row.
   * The reply is guarded by a token, so a slow answer from a previous library can never overwrite a
   * newer one, and it records which library it belongs to. Read-only: it never resimulates or writes.
   */
  const loadAverageLeaders = useCallback(
    async (library: string | null) => {
      const host = api;
      const token = ++averageLeadersToken.current;
      const fetchAverages = host?.overview_average_leaders;
      if (!host || typeof fetchAverages !== "function") {
        setAverageLeaders(null);
        setAverageLeadersLibrary(library);
        setAverageLeadersError("this host build does not expose overview_average_leaders yet");
        return;
      }
      try {
        const result = await fetchAverages();
        if (token !== averageLeadersToken.current) return;
        setAverageLeadersLibrary(library);
        if (!result || result.ok === false) {
          setAverageLeaders(null);
          setAverageLeadersError(result?.error ?? "the host returned no average leaders");
          return;
        }
        setAverageLeaders(result);
        setAverageLeadersError(null);
      } catch (error) {
        if (token === averageLeadersToken.current) {
          setAverageLeaders(null);
          setAverageLeadersLibrary(library);
          setAverageLeadersError(String(error));
        }
      }
    },
    [api],
  );

  /**
   * The average columns are the one overview read that costs the host a real aggregate query, so
   * they are refreshed on a bounded cadence instead of on every `status` tick: once when the library
   * opens or changes, then every {@link AVERAGE_REFRESH_INTERVAL_MS} while a search is running, plus
   * one last read when it stops - so a mean that improved just as the search finished still lights.
   * The lifetime maxima ride along on `status` and stay current without any extra call.
   */
  useEffect(() => {
    if (!api || !status?.library) return;
    const library = status.library;
    void loadAverageLeaders(library);
    if (status.state !== "Running") return;
    const timer = window.setInterval(() => {
      void loadAverageLeaders(library);
    }, AVERAGE_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [api, status?.library, status?.state, loadAverageLeaders]);

  /** The average leaders keyed by `"<encounterId>:<defeatCount>"`, for the overview's own rows. */
  const averageLeadersByFight = useMemo(() => {
    const map = new Map<string, { earned: AverageLeader | null; potential: AverageLeader | null }>();
    for (const leader of averageLeaders?.leaders ?? []) {
      map.set(`${leader.encounterId}:${leader.defeatCount}`, {
        earned: leader.highestAvgEarned ?? null,
        potential: leader.highestAvgPotential ?? null,
      });
    }
    for (const leader of status?.encounterAverageLeaders?.leaders ?? []) {
      map.set(`${leader.encounterId}:${leader.defeatCount}`, {
        earned: leader.highestAvgEarned ?? null,
        potential: leader.highestAvgPotential ?? null,
      });
    }
    return map;
  }, [averageLeaders, status?.encounterAverageLeaders]);
  /**
   * The four numbers the Encounter overview shows, keyed by encounter/difficulty/metric. Only the
   * readings are here - never a sample count - so the improvement highlight reacts to a new lifetime
   * record or a new leading mean and ignores a run that only grew a mean's `n`.
   */
  const encounterMetricReadings = useMemo<RecordReadings>(() => {
    const readings: RecordReadings = {};
    for (const campaign of campaigns) {
      for (const difficulty of campaign.difficulties) {
        const fight = `${difficulty.encounterId}:0`;
        const stat = encounterStats[fight];
        const averages = averageLeadersByFight.get(fight);
        readings[recordCellKey(difficulty.encounterId, 0, "highest-potential")] =
          stat?.highestPotentialChests ?? null;
        readings[recordCellKey(difficulty.encounterId, 0, "highest-earned")] =
          stat?.highestChestsEarned ?? null;
        readings[recordCellKey(difficulty.encounterId, 0, "avg-potential")] =
          averages?.potential?.mean ?? null;
        readings[recordCellKey(difficulty.encounterId, 0, "avg-earned")] = averages?.earned?.mean ?? null;
      }
    }
    return readings;
  }, [campaigns, encounterStats, averageLeadersByFight]);
  /**
   * The baseline is only trustworthy once the open library's own first data has landed: `status`
   * (which carries the lifetime maxima) has named the library, and the average leaders read has
   * answered for that same library. Until then nothing can light.
   */
  const averageLeadersReady = Boolean(status?.library) && averageLeadersLibrary === status?.library;
  const highlightedCells = useRecordHighlights(
    status?.library ?? null,
    encounterMetricReadings,
    averageLeadersReady,
  );
  /** The simulation safety limit in force, for the wording that names it. */
  const horizonTicks = status?.horizonTicks ?? null;
  /** Encounter identity from the host catalogue; every consumer degrades to the bare id. */
  const encounters = status?.encounters ?? null;
  /**
   * The worker counts this machine is actually allowed to run. The host publishes its own
   * `worker_ceiling()` (min(24, logical CPUs - 4), never below 1); the dropdown is derived from it,
   * so no machine is ever offered a count it cannot launch. While the ceiling is unknown the full
   * measured list is shown, because the host still clamps on Start.
   */
  const workerCeiling = status?.throughput?.ceiling ?? null;
  const allowedWorkers: number[] = useMemo(() => {
    const allowed = WORKER_CHOICES.filter((count) => workerCeiling == null || count <= workerCeiling);
    return allowed.length ? allowed : [WORKER_CHOICES[0]];
  }, [workerCeiling]);
  useEffect(() => {
    // Keep the visible selection inside the offered list, so the trigger never goes blank when a
    // saved preference is higher than this machine's ceiling.
    if (allowedWorkers.includes(workers)) return;
    setWorkers(allowedWorkers[allowedWorkers.length - 1]);
  }, [allowedWorkers, workers]);
  const hostStartedDuty = status?.throughput?.duty;
  useEffect(() => {
    if (status?.state === "Running" && hostStartedDuty != null) setDuty(hostStartedDuty);
  }, [status?.state, status?.library, hostStartedDuty]);
  const hostStartedWorkers = status?.throughput?.workers;
  useEffect(() => {
    // Hydrate the persisted native host preference on reopen even when the host is stopped.
    // While running, this also reflects an auto-started host's configured count.
    if (hostStartedWorkers != null && allowedWorkers.includes(hostStartedWorkers)) {
      setWorkers(hostStartedWorkers);
    }
  }, [status?.state, status?.library, hostStartedWorkers, allowedWorkers]);

  const selected = useMemo(
    () => candidates.find((candidate) => candidate.id === selectedId) ?? null,
    [candidates, selectedId],
  );

  useEffect(() => {
    if (selectedId && candidates.some((candidate) => candidate.id === selectedId)) return;
    setSelectedId(candidates.length ? candidates[0].id : null);
  }, [candidates, selectedId]);

  const exampleLabels = useMemo(
    () => (selected ? Object.keys(selected.examples ?? {}) : []),
    [selected],
  );
  /** The fight the selected strategy is for; every run card shows it, not just the card header. */
  const selectedEncounterId = encounterIdOf(selected);
  const selectedEncounter = selectedEncounterId === null
    ? undefined
    : encounters?.[String(selectedEncounterId)];
  const selectedEncounterRoster = encounterRoster(selectedEncounter);
  const party = partyOf(selected);

  useEffect(() => {
    if (!selected) {
      setExampleLabel(null);
      return;
    }
    const labels = Object.keys(selected.examples ?? {});
    setExampleLabel((current) => (current && labels.includes(current) ? current : labels[0] ?? null));
  }, [selected]);

  const families = useMemo(() => {
    const grouped = new Map<string, Candidate[]>();
    for (const candidate of candidates) {
      const key = candidate.family ?? NO_FAMILY;
      const bucket = grouped.get(key);
      if (bucket) bucket.push(candidate);
      else grouped.set(key, [candidate]);
    }
    return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [candidates]);

  const region = useMemo(() => regionFor(candidates, selected?.family ?? null), [candidates, selected]);

  /**
   * The distinct strategy families in this library, and the ones the encounter filter shows. This is
   * the main view now: the raw candidate list is still reachable, but only inside Technical details.
   */
  const strategyFamilies = useMemo(() => buildStrategyFamilies(candidates), [candidates]);
  const visibleFamilies = useMemo(
    () =>
      encounterFilter === "all"
        ? strategyFamilies
        : strategyFamilies.filter((family) => String(family.encounterId) === encounterFilter),
    [strategyFamilies, encounterFilter],
  );
  const toggleFamily = useCallback((key: string) => {
    setExpandedFamilies((current) =>
      current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key],
    );
  }, []);

  /**
   * Focus one encounter/difficulty from the Encounter Overview: the families section and the run
   * cards below follow it, and the previous family selection is dropped because it belonged to
   * another fight.
   */
  const focusFight = useCallback((encounterId: number) => {
    setWorkspaceView("strategies");
    setComparisonIds([]);
    setFightKey(`${encounterId}:0`);
    setEncounterFilter(String(encounterId));
    setFamilyKey(null);
    // The ranked list is display:none in the Overview workspace. Wait until React has switched
    // workspaces before scrolling to it; scrolling the hidden card leaves the old overview offset
    // in place, which can put the newly shortened page below all of its content.
    requestAnimationFrame(() => {
      const node = encounterStrategiesRef.current;
      if (node?.getClientRects().length && typeof node.scrollIntoView === "function") {
        node.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }, []);

  /** Select exactly one family; its representative becomes the strategy the detail cards show. */
  const selectFamily = useCallback((family: StrategyFamily) => {
    setFamilyKey(family.key);
    setSelectedId(family.representative.id);
    setExampleLabel(null);
  }, []);

  /** The one selected family, and the fight the overview has focused (both drive the scope wording). */
  const selectedFamily = useMemo(
    () => (familyKey ? strategyFamilies.find((family) => family.key === familyKey) ?? null : null),
    [strategyFamilies, familyKey],
  );
  const focusedEncounterId = useMemo(() => {
    if (fightKey) return fightKey.split(":")[0];
    if (selectedFamily?.encounterId != null) return String(selectedFamily.encounterId);
    return null;
  }, [fightKey, selectedFamily]);
  const fightLabel = useMemo(() => {
    if (focusedEncounterId == null) return null;
    const encounterId = Number(focusedEncounterId);
    const encounter = encounters?.[focusedEncounterId];
    // Name the fight the way the overview card does: battle line + difficulty, so the reader sees the
    // same label above the family list and above the run cards.
    const campaign = campaigns.find((entry) =>
      entry.difficulties.some((difficulty) => difficulty.encounterId === encounterId));
    const difficulty = campaign?.difficulties.find((entry) => entry.encounterId === encounterId);
    const name = encounterLabel(encounterId, encounter);
    return {
      encounter: campaign ? campaign.title : name,
      difficulty: difficulty?.label ?? null,
      title: difficulty?.title ?? encounter?.title ?? name,
    };
  }, [focusedEncounterId, encounters, campaigns]);

  /**
   * The live search focus, read straight from the host: the exact encounter every *new* attempt is
   * spent on, or null for the normal distribution. The per-row toggle and the banner's clear control
   * both drive the selected encounter set; an empty set means all encounters, and it survives every status
   * poll because it is part of the host's own snapshot.
   */
  const searchFocusIds = useMemo(() => status?.focusEncounters ?? (status?.focusEncounter != null ? [status.focusEncounter] : []), [status?.focusEncounters, status?.focusEncounter]);
  const searchFocus = searchFocusIds.length === 1 ? searchFocusIds[0] : null;
  const [guideEncounter, setGuideEncounter] = useState<number | null>(null);
  const activeGuideEncounter = status?.encounterAware?.encounter;
  const guideScope = guideEncounter ?? (typeof activeGuideEncounter === 'number' ? activeGuideEncounter : 0);
  const loadGuide = useCallback(() => api?.encounter_player_regions?.(null, guideScope) ?? Promise.resolve(null), [api, guideScope]);
  /** The focused fight, labelled the way its own overview row is, for the banner. */
  const searchFocusLabel = useMemo(() => {
    if (searchFocusIds.length > 1) return `${searchFocusIds.length} selected encounters`;
    if (searchFocus == null) return null;
    const encounter = encounters?.[String(searchFocus)];
    const campaign = campaigns.find((entry) =>
      entry.difficulties.some((difficulty) => difficulty.encounterId === searchFocus));
    const difficulty = campaign?.difficulties.find((entry) => entry.encounterId === searchFocus);
    return difficulty?.title ?? encounter?.title ?? encounterLabel(searchFocus, encounter);
  }, [searchFocus, searchFocusIds, encounters, campaigns]);
  /** Whether this library actually holds a build for the focused fight, so the banner can say so. */
  const searchFocusHeld = useMemo(
    () => searchFocus != null && (status?.libraryEncounters ?? []).includes(searchFocus),
    [searchFocus, status],
  );
  /**
   * The host's actionable stall sentence for a Running focused search with nothing to dispatch, or
   * null. `idleReason` is only ever populated for that exact case - a busy pool and a deliberate
   * duty-cycle idle both leave it null - so it is rendered as-is rather than re-derived here. It is
   * shown only while the focus still exists and the snapshot is fresh, so a stale reading or a
   * cleared focus drops it without waiting for the host to say so again.
   */
  const searchIdleReason = status?.idleReason ?? null;
  const focusedSearchStalled = Boolean(
    searchIdleReason && status?.state === "Running" && searchFocus != null && !statusStale,
  );

  /**
   * The four lane leaders of the focused fight, in the order the objective names them. A lane with
   * no member is simply absent - that is how a library whose runs predate the stored-attack metrics
   * shows no setup leader instead of an invented one.
   */
  const laneLeaders = useMemo(() => {
    if (!fightKey) return [];
    const table = status?.eliteLanes?.[fightKey] ?? {};
    const definitions: { lane: LaneName; title: string; purpose: string }[] = [
      { lane: "earned", title: "Best earned", purpose: "proven conversion" },
      { lane: "potential", title: "Biggest unearned prize", purpose: "reached, not yet cashed" },
      { lane: "setup", title: "Best stored-attack setup", purpose: "closest to the multi-chest chain" },
      { lane: "efficiency", title: "Best efficiency", purpose: "same yield, fewer consumables" },
    ];
    return definitions.flatMap((definition) => {
      const leader = table[definition.lane];
      if (!leader) return [];
      return [{ ...definition, leader, reading: laneReading(definition.lane, leader) }];
    });
  }, [status, fightKey]);

  const completeSelection = candidates.filter(isCompleteSelection).length;
  const provenance = status?.provenance ?? null;

  /** The host's measured probes, kept whole and split by which strategy each one belongs to. */
  const probes = useMemo(() => status?.probes ?? [], [status]);
  const lastProbe = status?.lastProbe ?? null;
  const selectedProbes = useMemo(
    () => probes.filter((probe) => probe.pivotId === selected?.id),
    [probes, selected],
  );
  const otherProbes = useMemo(
    () => probes.filter((probe) => probe.pivotId !== selected?.id),
    [probes, selected],
  );

  /** Chest-first only when the runner actually reports a retained sample for some strategy. */
  const anyRetained = useMemo(
    () => candidates.some((candidate) => retainedReading(candidate.selection).known),
    [candidates],
  );
  /** True as soon as any strategy has a resolved run, which is all a chest mean needs. */
  const anyChests = useMemo(
    () => candidates.some((candidate) => candidateChests(candidate) !== null),
    [candidates],
  );
  const rankedCandidates = useMemo(
    () => (anyRetained
      ? rankByChestYield(candidates)
      : anyChests ? rankByChestsPerRun(candidates) : rankByWinLowerBound(candidates)),
    [anyRetained, anyChests, candidates],
  );
  const headline = selected ?? rankedCandidates[0] ?? null;
  const headlineRetained = retainedReading(headline?.selection);
  const headlineChests = candidateChests(headline);
  const headlineWin = candidateWin(headline);
  /**
   * The best chests-per-run mean anywhere in the library, with its own precision. Per-run chest
   * counts vary far more than the differences between builds, so a strategy whose 95% interval
   * overlaps this one is not distinguishable from "the best" yet - saying so is the difference
   * between a ranking and a guess.
   */
  const bestChests = useMemo(() => {
    let best: { mean: number; se: number | null } | null = null;
    for (const candidate of candidates) {
      const reading = candidateChests(candidate);
      if (!reading) continue;
      if (!best || reading.mean > best.mean) best = { mean: reading.mean, se: reading.se };
    }
    return best;
  }, [candidates]);
  const withinNoiseOfBest = useCallback(
    (reading: { mean: number; se: number | null } | null | undefined) => {
      if (!reading || !bestChests?.se || reading.se == null) return false;
      const low = bestChests.mean - 1.96 * bestChests.se;
      return reading.mean + 1.96 * reading.se >= low;
    },
    [bestChests],
  );
  const encountersById = useMemo(() => {
    const counts = new Map<number, number>();
    for (const candidate of candidates) {
      const id = encounterIdOf(candidate);
      if (id !== null) counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    return counts;
  }, [candidates]);
  const encounterIds = useMemo(
    () => [...encountersById.keys()].sort((left, right) => left - right),
    [encountersById],
  );
  /**
   * The real encounter choices the encounter-aware panel scopes a preview to, built from the
   * recovered catalogue the overview already shows. Never hardcoded: it is empty when the host
   * publishes no catalogue, and the panel then keeps its scope control disabled.
   */
  const encounterChoices = useMemo<EncounterChoice[]>(() => {
    const byId = new Map<number, EncounterChoice>();
    for (const campaign of campaigns) {
      for (const difficulty of campaign.difficulties) {
        if (!Number.isInteger(difficulty.encounterId)) continue;
        const info = encounters?.[String(difficulty.encounterId)];
        const title = difficulty.title || info?.title || campaign.title;
        const boss = difficulty.boss ?? info?.boss ?? campaign.boss ?? null;
        const level = difficulty.level ?? info?.level ?? null;
        const enemyCount = difficulty.enemyCount ?? info?.enemyCount ?? null;
        const label = `Encounter ${difficulty.encounterId} · ${title}${
          difficulty.label ? ` (${difficulty.label})` : ""
        }`;
        byId.set(difficulty.encounterId, {
          id: difficulty.encounterId,
          label,
          title: title || null,
          boss: boss ?? null,
          level: typeof level === "number" ? level : null,
          enemyCount: typeof enemyCount === "number" ? enemyCount : null,
        });
      }
    }
    return [...byId.values()].sort((left, right) => left.id - right.id);
  }, [campaigns, encounters]);
  const visibleCandidates = useMemo(
    () => (encounterFilter === "all"
      ? rankedCandidates
      : rankedCandidates.filter((candidate) => String(encounterIdOf(candidate)) === encounterFilter)),
    [rankedCandidates, encounterFilter],
  );
  /** Highest retained count among this strategy's stored runs, or null while none is measured. */
  const exampleRetainedMax = useMemo(() => {
    const values = Object.values(selected?.examples ?? {})
      .map((example) => retainedRunValue(example))
      .filter((value): value is number => value !== null);
    return values.length ? Math.max(...values) : null;
  }, [selected]);
  /** Highest chest count among this strategy's stored runs, so "most chests" is a real maximum. */
  const exampleChestsMax = useMemo(() => {
    const values = Object.values(selected?.examples ?? {})
      .map((example) => chestReading(example))
      .filter((reading): reading is Extract<ChestReading, { known: true }> => reading.known)
      .map((reading) => reading.count);
    return values.length ? Math.max(...values) : null;
  }, [selected]);
  const headlineWinLower = (() => {
    const value = headline?.selection?.winInterval?.[0];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  })();
  const headlineAward = (() => {
    const readings = Object.values(headline?.examples ?? {}).map((example) => awardReading(example));
    const known = readings.filter(
      (reading): reading is Extract<AwardReading, { known: true }> => reading.known,
    );
    return {
      total: readings.length,
      known: known.length,
      awarded: known.reduce((sum, reading) => sum + reading.count, 0),
      certified: known.filter((reading) => reading.certified).length,
    };
  })();

  const encounterAware = status?.encounterAware ?? null;
  const encounterAwareActive = encounterAware?.enabled === true;

  const sendCommand = useCallback(
    async (label: string, action: string, value: Record<string, unknown>) => {
      const host = api;
      if (!host) return;
      setBusy(label);
      setActionError(null);
      try {
        const result = await host.command(action, value);
        if (result && result.ok === false) {
          const message = result.error ?? `${label} was refused`;
          // The native bridge returns this when the queued command has not completed within its
          // acknowledgement window. It is still running and its eventual result arrives in status;
          // presenting it as a refusal invites duplicate clicks while the first command is active.
          if (/command is still pending/i.test(message)) {
            setNotice(`${label} is still processing; its result will appear in optimiser status.`);
          } else {
            setActionError(message);
          }
        }
        else setNotice(`${label} accepted`);
      } catch (error) {
        setActionError(String(error));
      } finally {
        setBusy(null);
      }
    },
    [api],
  );

  /**
   * Point every new attempt at one exact encounter, or clear the focus. One command, then an immediate
   * refresh so the pressed toggle and the banner follow without waiting for the next poll. The host
   * validates the id and owns the resulting value; nothing here keeps a second local copy of it.
   */
  const setSearchFocus = useCallback(
    async (encounterId: number | null) => {
      await sendCommand(
        encounterId == null ? "Clear search focus" : `Focus encounter ${encounterId}`,
        "focus_encounter",
        { encounterIds: encounterId == null ? [] : searchFocusIds.includes(encounterId) ? searchFocusIds.filter(id => id !== encounterId) : [...searchFocusIds, encounterId] },
      );
      await refresh();
    },
    [sendCommand, refresh, searchFocusIds],
  );

  /**
  /**
   * Start a probe on one exact build. The host chooses the ladder and owns the refusal reason; this
   * only states which axis (or axis pair) is being asked for and, for an unestablished input, that
   * the user opted in to exploring it. Nothing about the ladder is decided here.
   */
  const startProbeFor = useCallback(async (candidateId: string) => {
    const value: Record<string, unknown> = { candidateId, axis: probeAxis };
    if (probeAxis2 !== NO_SECOND_AXIS) value.axis2 = probeAxis2;
    if (
      allowUnestablished &&
      (!axisIsEstablished(probeAxis) || (probeAxis2 !== NO_SECOND_AXIS && !axisIsEstablished(probeAxis2)))
    ) {
      value.unestablished = true;
    }
    await sendCommand(`Probe ${axisName(probeAxis)}`, "probe", value);
  }, [allowUnestablished, probeAxis, probeAxis2, sendCommand]);

  /** Start a probe on the page's currently selected strategy. */
  const startProbe = useCallback(async () => {
    if (!selected) return;
    await startProbeFor(selected.id);
  }, [selected, startProbeFor]);

  /**
   * Turning the unestablished-input opt-in off drops any axis it was hiding, so the selects never
   * point at a value the list no longer offers.
   */
  const handleAllowUnestablished = useCallback((on: boolean) => {
    setAllowUnestablished(on);
    if (!on && !axisIsEstablished(probeAxis)) setProbeAxis(PROBE_AXES[0]);
    if (!on && probeAxis2 !== NO_SECOND_AXIS && !axisIsEstablished(probeAxis2)) {
      setProbeAxis2(NO_SECOND_AXIS);
    }
  }, [probeAxis, probeAxis2]);

  const importBuild = useCallback(async () => {
    const host = api;
    if (!host) return;
    setBusy("Import");
    setActionError(null);
    try {
      const result = await host.import_build();
      if (result && result.ok === false) setActionError(result.error ?? "the supplied build was refused");
      else setNotice("imported the supplied build");
    } catch (error) {
      setActionError(String(error));
    } finally {
      setBusy(null);
    }
  }, [api]);

  const exportBuild = useCallback(async () => {
    const host = api;
    if (!host || !selected) return;
    setBusy("Export");
    setActionError(null);
    try {
      const result = await host.export_build(selected.id);
      if (result && result.ok === false) setActionError(result.error ?? "the build could not be exported");
      else setNotice(`exported ${selected.label}`);
    } catch (error) {
      setActionError(String(error));
    } finally {
      setBusy(null);
    }
  }, [api, selected]);

  const newLibrary = useCallback(async () => {
    const host = api;
    if (!host) return;
    setBusy("New library");
    setActionError(null);
    try {
      const result = await host.new_library();
      if (result && result.ok === false) setActionError(result.error ?? "a new library was refused");
      // Naming a file that already exists opens it rather than replacing it, and the host says so:
      // the operating system's "replace?" prompt is the save dialog's, not this action's intent.
      else if (result?.existed) setNotice(result.notice ?? "the host opened the existing library");
      else setNotice("the host opened a new library");
    } catch (error) {
      setActionError(String(error));
    } finally {
      setBusy(null);
    }
  }, [api]);

  /**
   * Open an existing library file. The host owns the file picker and the switch; this only reports
   * what it did. Nothing is replaced or deleted, so there is no confirmation step to ask for.
   */
  const openLibrary = useCallback(async () => {
    const host = api;
    if (!host?.open_library) return;
    setBusy("Open library");
    setActionError(null);
    try {
      const result = await host.open_library();
      if (result && result.ok === false) setActionError(result.error ?? "the library could not be opened");
      else if (result?.unchanged) setNotice("that library is already open");
      else if (result?.library) setNotice(`opened ${result.library.split(/[\\/]/).pop()}`);
      else setNotice("no library chosen");
    } catch (error) {
      setActionError(String(error));
    } finally {
      setBusy(null);
    }
  }, [api]);

  /**
   * Run one host replay call and show its trace, read-only. `scenarioJson` is omitted so the shared
   * renderer shows the selected actual trace and never offers branch interactions for an optimiser
   * replay; the visual identity is still supplied, because without it the renderer cannot know what
   * job these research fighters are and draws placeholders instead of the units the run actually used.
   * Every replay control shares this path, so a refusal is reported exactly as the host worded it.
   */
  /**
   * Start an on-demand diagnostic export. The host owns the save dialog and the 500 MB cap; this only
   * forwards the chosen number of most-recent battles and then polls the job's cached progress once a
   * second while it runs. It never pauses the search.
   */
  const startDiagnosticExport = useCallback(async () => {
    const host = api;
    if (!host?.export_diagnostics) return;
    const draft = exportCountInput.trim();
    const parsed = draft === "" ? null : Number(draft);
    if (draft !== "" && (!Number.isInteger(parsed) || (parsed as number) < 1
        || (parsed as number) > DIAGNOSTIC_EXPORT_MAX)) {
      setExportError(`Enter a whole number between 1 and ${formatInt(DIAGNOSTIC_EXPORT_MAX)}.`);
      return;
    }
    setExportStarting(true);
    setExportError(null);
    try {
      const result = await host.export_diagnostics(parsed ?? undefined);
      if (result && result.ok === false) {
        setExportError(result.error ?? "the diagnostic export was refused");
        return;
      }
      if (result?.cancelled) {
        setNotice("no diagnostic export was saved");
        return;
      }
      if (result?.jobId) {
        setExportJobId(result.jobId);
        setExportStatus({
          ok: true, jobId: result.jobId, state: "running", stageLabel: "Starting",
          path: result.path, included: 0,
        });
        setNotice("saving the diagnostic export");
      }
    } catch (error) {
      setExportError(String(error));
    } finally {
      setExportStarting(false);
    }
  }, [api, exportCountInput]);

  /**
   * Poll the export's cached status once a second, and only while it is running. The moment it
   * finishes or fails the timer stops, so an idle page pays nothing and a running search is never
   * paused. Progress is shown beside the button; no separate window is opened.
   */
  useEffect(() => {
    const host = api;
    if (!host?.export_diagnostics_status || !exportJobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const next = await host.export_diagnostics_status!(exportJobId);
        if (cancelled) return;
        setExportStatus(next ?? null);
        if (next?.state === "running") {
          timer = setTimeout(() => void tick(), 1000);
        } else if (next?.state === "error") {
          setExportError(next.error ?? "the diagnostic export failed");
          setExportJobId(null);
        } else if (next?.state === "done") {
          setNotice(`saved ${(next.path ?? "the diagnostic export").split(/[\\/]/).pop()}`);
          setExportJobId(null);
        }
      } catch (error) {
        if (!cancelled) {
          setExportError(String(error));
          setExportJobId(null);
        }
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [api, exportJobId]);

  const presentReplay = useCallback(
    async (call: () => Promise<ReplayResult>, title: string, target?: InvestigationTarget) => {
      replayReturnScroll.current = window.scrollY;
      setReplayBusy(true);
      setReplayContext(null);
      setReplayError(null);
      // A recorded-run replay is not a fresh simulation, so the visual note never carries over.
      setVisualNote(null);
      try {
        const result = await call();
        if (!result || result.ok === false || !result.replay) {
          setReplayError(result?.error ?? "the host returned no replay payload");
          return;
        }
        const { result: parsed, issues } = parseBattleReplayResult(result.replay);
        if (!parsed) {
          setReplayError(`replay payload rejected: ${issues.join("; ")}`);
          return;
        }
        setReplayRecord(createGeneratedBattleRecord(
          parsed,
          result.visualSetup ?? undefined,
          [...REPLAY_WARNINGS, ...(result.warnings ?? [])],
          title,
        ));
        setReplayTitle(title);
        setShowReplay(true);
        window.scrollTo({ top: 0, behavior: "auto" });
        if (target && api?.strategy_detail) {
          try {
            const detail = await api.strategy_detail(target.candidateId ?? null, target.holderKey ?? null);
            const related = (status?.fineTune ?? []).filter(program => program.parent.candidateId === detail.candidateId || program.points?.some(point => point.candidateId === detail.candidateId));
            const programs = related.length ? related : (await api.fine_tune_programs?.(detail.candidateId)) ?? [];
            setReplayContext({ detail: { ...detail, scenario: result.scenario ?? detail.scenario, formation: result.formation ?? detail.formation }, target, programs });
          } catch { /* The verified replay remains usable if build context is unavailable. */ }
        }
      } catch (error) {
        setReplayError(String(error));
      } finally {
        setReplayBusy(false);
      }
    },
    [api, status?.fineTune],
  );

  const replaySeeds = useCallback(
    async (candidateId: string, seeds: SeedPair, title: string) => {
      const host = api;
      if (!host) return;
      await presentReplay(() => host.replay(candidateId, seeds), title, { candidateId });
    },
    [api, presentReplay],
  );

  const runReplay = useCallback(async (label?: string, candidate?: Candidate | null) => {
    const chosen = candidate ?? selected;
    const target = label ?? exampleLabel;
    if (!chosen || !target) return;
    const seeds = chosen.examples?.[target]?.seeds;
    if (!Array.isArray(seeds) || seeds.length < 2) {
      setReplayError("this example carries no seed pair to replay");
      return;
    }
    await replaySeeds(chosen.id, [Number(seeds[0]), Number(seeds[1])], `${chosen.label} · ${target}`);
  }, [replaySeeds, selected, exampleLabel]);

  /**
   * Replay a persisted encounter-wide record holder. The host keeps the frozen scenario with the
   * record, so this still works after the candidate that set it has been pruned.
   */
  const runHolderReplay = useCallback(
    async (key: string, label: string) => {
      const host = api;
      if (!host) return;
      await presentReplay(() => host.replay_holder(key), label, { holderKey: key });
    },
    [api, presentReplay],
  );

  const watchRun = useCallback(
    (label: string) => {
      setExampleLabel(label);
      void runReplay(label);
    },
    [runReplay],
  );

  /**
   * Watch the best stored run straight from a family row. The replay needs the candidate itself, so
   * it is passed in instead of read from state, which has not re-rendered when this fires.
   */
  const watchFamilyRun = useCallback(
    (member: Candidate) => {
      const target = bestStoredRunLabel(member);
      setSelectedId(member.id);
      if (target) setExampleLabel(target);
      void runReplay(target ?? undefined, member);
    },
    [runReplay],
  );

  /* ------------------------------------------------------------------ */
  /* Encounter strategies, records and the investigation panel           */
  /* ------------------------------------------------------------------ */

  const strategies = useMemo(() => encounterStrategies?.strategies ?? [], [encounterStrategies]);
  const orphanHolders = useMemo(() => encounterStrategies?.orphanHolders ?? [], [encounterStrategies]);
  const rankedStrategies = useMemo(
    () => [...strategies].sort((left, right) => compareEncounterStrategies(left, right, strategySort)),
    [strategies, strategySort],
  );
  /** Every ranked row the host labelled as a probe variant, whatever the sort mode. */
  const probeVariantCount = useMemo(
    () => rankedStrategies.filter(isProbeStrategyRow).length,
    [rankedStrategies],
  );
  /**
   * What the ranked list renders: the principal strategies by default, the whole ranked list once
   * the reader opts in. Rank numbers follow the rendered order, so hiding probes does not leave
   * gaps and the principal strategies read as 1, 2, 3.
   */
  const visibleStrategies = useMemo(
    () => (showProbeVariants ? rankedStrategies : rankedStrategies.filter((row) => !isProbeStrategyRow(row))),
    [rankedStrategies, showProbeVariants],
  );
  /** The hidden-probe sentence uses the same count and noun in both the hint and the only-probe state. */
  const probeVariantLabel = `${formatInt(probeVariantCount)} range-test probe variant${
    probeVariantCount === 1 ? "" : "s"
  }`;

  /**
   * The focused fight's search portfolio, read only when the host published one, the snapshot is
   * fresh, and the ranked list's own fight is the fight the search is actually focused on. The active
   * focus is a snapshot primitive, so a portfolio left over from a previously focused fight can never
   * be shown against another fight's ranked list, and a poll that republishes the same plan changes
   * nothing on screen. With no portfolio the Ranked strategies card renders exactly as before.
   */
  const portfolioPlan = status?.focusPortfolio ?? null;
  const portfolioFight = useMemo(() => {
    if (!portfolioPlan || statusStale) return null;
    const focused = status?.focusEncounter ?? null;
    if (focused == null || !fightKey) return null;
    return fightKey.split(":")[0] === String(focused) ? fightKey : null;
  }, [portfolioPlan, status?.focusEncounter, statusStale, fightKey]);
  /** Ranked rows by candidate id, so the graduated tier reports the same metrics as the table below. */
  const rankedById = useMemo(
    () => new Map(rankedStrategies.map((row) => [row.candidateId, row])),
    [rankedStrategies],
  );

  /**
   * The ranked row an overview metric click owns. Resolution waits until the payload for that exact
   * fight has landed, so a stale list from the previously focused fight is never matched. A lifetime
   * maximum is owned by its frozen holder's candidate; if that candidate is gone, the matching
   * orphan-holder row is the owner instead, and its frozen detail stays reachable. Nothing else is
   * ever substituted: an owner that cannot be found produces an explicit explanation rather than a
   * highlight on a different build.
   */
  const overviewMetricOwner = useMemo<OverviewMetricOwner>(() => {
    const focus = overviewMetricFocus;
    const pending: OverviewMetricOwner = { pending: true, row: null, holder: null, missing: null };
    if (!focus) return pending;
    if (!encounterStrategies || strategiesBusy || strategiesError) return pending;
    if (strategiesFight !== `${focus.encounterId}:0`) return pending;
    if (focus.candidateId) {
      const row = rankedStrategies.find((entry) => entry.candidateId === focus.candidateId);
      if (row) return { pending: false, row, holder: null, missing: null };
    }
    if (focus.holderKey) {
      const holder = orphanHolders.find((entry) => entry.key === focus.holderKey);
      if (holder) return { pending: false, row: null, holder, missing: null };
    }
    const owner = focus.holderKey
      ? `the frozen record holder ${focus.holderKey} (its candidate is no longer recorded)`
      : `candidate ${shortId(focus.candidateId ?? "")}`;
    return {
      pending: false,
      row: null,
      holder: null,
      missing:
        `This fight's ranked list holds no row for ${owner}, so no build is highlighted. ` +
        "Open the reading's own investigation directly below.",
    };
  }, [
    overviewMetricFocus,
    encounterStrategies,
    strategiesFight,
    strategiesBusy,
    strategiesError,
    rankedStrategies,
    orphanHolders,
  ]);

  /**
   * A probe variant the reader has not opted into would otherwise stay hidden, so the metric click
   * reveals it instead of silently failing to scroll to a row that is not rendered.
   */
  useEffect(() => {
    const owner = overviewMetricOwner;
    if (owner.pending || !owner.row) return;
    if (isProbeStrategyRow(owner.row) && !showProbeVariants) setShowProbeVariants(true);
  }, [overviewMetricOwner, showProbeVariants]);

  /**
   * Bring the owning ranked row - or orphan-holder row - into view and move keyboard focus onto its
   * own open control, so a reader who clicked the reading with the keyboard can act on it without
   * hunting. The `nonce` keeps a fresh click re-scrolling even when it targets the same row, while a
   * re-sort of the same focus does not yank the page.
   */
  useEffect(() => {
    const focus = overviewMetricFocus;
    const owner = overviewMetricOwner;
    if (!focus || owner.pending) return;
    const selector = owner.row
      ? `[data-strategy-row="${CSS.escape(owner.row.candidateId)}"]`
      : owner.holder
        ? `[data-strategy-orphan="${CSS.escape(owner.holder.key)}"]`
        : null;
    if (!selector) return;
    const marker = `${focus.nonce}:${selector}`;
    if (overviewMetricScrollRef.current === marker) return;
    const node = encounterStrategiesRef.current?.querySelector<HTMLElement>(selector) ?? null;
    if (!node) return;
    overviewMetricScrollRef.current = marker;
    const frame = requestAnimationFrame(() => {
      const control =
        node.querySelector<HTMLElement>('[data-action="select-encounter-strategy"]') ??
        node.querySelector<HTMLElement>('[data-action="investigate-orphan-holder"]');
      if (control && typeof control.focus === "function") control.focus({ preventScroll: true });
      node.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return () => cancelAnimationFrame(frame);
  }, [overviewMetricFocus, overviewMetricOwner, showProbeVariants]);

  /** Drop the overview highlight: another selection (a build name) supersedes the metric click. */
  const clearOverviewMetricFocus = useCallback(() => {
    overviewMetricScrollRef.current = null;
    setOverviewMetricFocus(null);
  }, []);

  const canEncounterStrategies = typeof api?.encounter_strategies === "function";
  const canRunStrategy = typeof api?.start_strategy_experiment === "function" || typeof api?.run_strategy === "function";
  const canSimulateVisual = typeof api?.simulate_strategy_visual === "function";
  /** Any fresh simulation in flight: the two trial controls and every run replay stand down for it. */
  const simulationRunning = trialRunning || visualRunning || Boolean(busy);
  /** A host replay is impossible while one is running, and equally so while a simulation holds the
   * host's single operation slot, so both block the same controls. */
  const replayBlocked = replayBusy || simulationRunning;
  /** The count box's own reading, clamped for the button's label and its request. */
  const trialCountEntry = parseTrialCount(trialCountInput);
  const trialCount = clampTrialCount(trialCountEntry ?? FRESH_TRIAL_DEFAULT);
  const trialCountInvalid = trialCountEntry !== null && trialCountEntry !== trialCount;

  /** The loaded payload, but only while it still belongs to the panel's current target. */
  const investigationDetail = useMemo(
    () =>
      investigation && sameInvestigationTarget(investigation.target, investigationTarget)
        ? investigation.detail
        : null,
    [investigation, investigationTarget],
  );
  const investigationCandidateId =
    investigationDetail?.candidateId ?? investigationTarget?.candidateId ?? null;
  const batchScope = JSON.stringify([status?.library ?? null, investigationCandidateId]);
  const scopedBatches = savedBatches?.scope === batchScope ? savedBatches : null;
  // Read persisted worker results on entry and while workers progress. Never attach a late reply
  // to a different library/build, and keep legacy trial cards available for older hosts.
  useEffect(() => {
    if (!api?.experiment_report || !investigationCandidateId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const result = await api.experiment_report!(investigationCandidateId);
        if (cancelled) return;
        if (!result.ok) throw new Error(result.error ?? "Saved batch results unavailable");
        setSavedBatches({ scope: batchScope, batches: result.batches ?? [],
          supported: Array.isArray(result.batches), error: null });
      } catch (error) {
        if (!cancelled) setSavedBatches(previous => ({ scope: batchScope,
          batches: previous?.scope === batchScope ? previous.batches : [],
          supported: previous?.scope === batchScope ? previous.supported : true,
          error: String(error) }));
      }
      if (!cancelled) timer = setTimeout(read, 5000);
    };
    void read();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, investigationCandidateId, batchScope, status?.focusedExperiment?.id,
    status?.focusedExperiment?.status]);
  const investigationCandidate = useMemo(
    () =>
      investigationCandidateId
        ? candidates.find((candidate) => candidate.id === investigationCandidateId) ?? null
        : null,
    [candidates, investigationCandidateId],
  );
  /** The frozen scenario that produced the record - the detail's own, else the live candidate's. */
  const investigationScenario = frozenInvestigationScenario(
    investigationDetail?.scenario,
    investigationCandidate?.scenario,
  );
  const investigationEncounterId = scenarioEncounterId(investigationScenario);
  const investigationParty = useMemo(() => scenarioParty(investigationScenario), [investigationScenario]);
  const investigationTeamBuild = useMemo(() => teamBuildSlots(investigationScenario), [investigationScenario]);
  const storedRuns = useMemo(() => investigationDetail?.storedRuns ?? [], [investigationDetail]);
  const trialRuns = useMemo(() => investigationDetail?.trialRuns ?? [], [investigationDetail]);
  const investigationProbes = useMemo(
    () =>
      investigationCandidateId
        ? probes.filter((probe) => probe.pivotId === investigationCandidateId)
        : [],
    [probes, investigationCandidateId],
  );

  /**
   * The parent whose fine-tuning programs this panel shows. Normally the investigated build itself;
   * when the investigated build is a tested point (or interaction cell) of a program, the panel
   * steps back to that program's frozen parent, so the section is always grouped under the parent and
   * offers the route back to it. The lineage is read from the host's own program views, never guessed
   * from a label.
   */
  const fineTuneChildOrigin = useMemo(() => {
    if (!investigationCandidateId) return null;
    for (const program of status?.fineTune ?? []) {
      const point = (program.points ?? []).some(
        (entry) => entry.candidateId === investigationCandidateId,
      );
      const cell = (program.cells ?? []).some(
        (entry) => entry.candidateId === investigationCandidateId,
      );
      if (point || cell) return program.parent;
    }
    return null;
  }, [status?.fineTune, investigationCandidateId]);
  const fineTuneParentId = fineTuneChildOrigin?.candidateId ?? investigationCandidateId;
  const fineTuneParentCandidate = useMemo(
    () =>
      fineTuneParentId
        ? candidates.find((candidate) => candidate.id === fineTuneParentId) ?? null
        : null,
    [candidates, fineTuneParentId],
  );
  const fineTuneParentLabel =
    fineTuneChildOrigin?.label ??
    fineTuneParentCandidate?.label ??
    (fineTuneParentId ? shortId(fineTuneParentId) : "no build");
  const fineTuneParentResident = Boolean(fineTuneParentCandidate);
  const canReadFineTunePrograms =
    typeof api?.fine_tune_programs === "function" || status?.fineTune != null;
  /** The parent's own programs: the fetched read when it belongs to this parent, else the snapshot. */
  const fineTunePrograms = useMemo(() => {
    if (fineTunedPrograms && fineTuneProgramsParent === fineTuneParentId) {
      return fineTunedPrograms.filter((program) => isCombatTuningAxis(program.axis));
    }
    return (status?.fineTune ?? []).filter(
      (program) =>
        program.parent.candidateId === fineTuneParentId && isCombatTuningAxis(program.axis),
    );
  }, [fineTunedPrograms, fineTuneProgramsParent, status?.fineTune, fineTuneParentId]);

  /**
   * Read one parent's fine-tuning programs through the host's read API. Bounded: it runs once when
   * the parent or library changes, then on {@link FINE_TUNE_REFRESH_MS} while the search is running,
   * plus one last read when it stops - never on every `status` tick.
   */
  const refreshFineTunePrograms = useCallback(
    async (parentId: string) => {
      const host = api;
      if (!host || typeof host.fine_tune_programs !== "function") return;
      const token = ++fineTuneToken.current;
      try {
        const programs = await host.fine_tune_programs(parentId);
        if (token !== fineTuneToken.current) return;
        setFineTunedPrograms(Array.isArray(programs) ? programs : []);
        setFineTuneProgramsParent(parentId);
        setFineTuneError(null);
      } catch (error) {
        if (token === fineTuneToken.current) setFineTuneError(String(error));
      }
    },
    [api],
  );

  useEffect(() => {
    if (!api || !fineTuneParentId) {
      setFineTunedPrograms(null);
      setFineTuneProgramsParent(null);
      return;
    }
    void refreshFineTunePrograms(fineTuneParentId);
    if (status?.state !== "Running") return;
    const timer = window.setInterval(() => {
      void refreshFineTunePrograms(fineTuneParentId);
    }, FINE_TUNE_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [api, fineTuneParentId, status?.state, refreshFineTunePrograms]);

  /** Clear a previous point's manual-command error when the panel moves to another build. */
  useEffect(() => {
    setFineTuneStartError(null);
  }, [fineTuneParentId]);

  /** One manual `fine_tune` command for the grouped parent: another statistic, optionally a target. */
  const startFineTune = useCallback(
    async (request: { axis: string; unit: string; target: number | null; samples: number }) => {
      const host = api;
      if (!host || !fineTuneParentId) return;
      setFineTuneStarting(true);
      setBusy("Starting refinement");
      setFineTuneStartError(null);
      try {
        const command = async (action: string, value: Record<string, unknown> = {}) => {
          const response = await host.command(action, value);
          if (response?.ok === false) throw new Error(response.error ?? `${action} was refused`);
        };
        await command("pause");
        const deadline = Date.now() + 60000;
        let snapshot = await host.status();
        while (snapshot.state === "Running" || snapshot.state === "Saving") {
          if (Date.now() > deadline) throw new Error("The current battles are still saving. Wait for Paused, then start refinement again.");
          await new Promise(resolve => setTimeout(resolve, 500));
          snapshot = await host.status();
        }
        const value: Record<string, unknown> = { candidateId: fineTuneParentId, axis: request.axis,
          budget: {pointBank: request.samples, maxBank: Math.max(32768, request.samples * 8), maxChildren: 64} };
        if (request.unit && request.unit !== FINE_TUNE_UNIT_AUTO) value.unit = request.unit;
        if (request.target !== null) value.targets = [Math.trunc(request.target)];
        const result = await host.command("fine_tune", value);
        if (result && result.ok === false) {
          setFineTuneStartError(result.error ?? "the host refused the fine-tuning program");
          return;
        }
        const encounterId = investigationEncounterId;
        if (encounterId == null) throw new Error("Program created, but this build has no encounter id. Choose a search focus before starting.");
        await command("focus_encounter", { encounterId });
        await command("start", { workers, duty });
        const started = await host.status();
        if (started.error) throw new Error(started.error);
        setNotice("Refinement started: the search is focused on this encounter and will allocate worker runs to the selected program.");
        await refreshFineTunePrograms(fineTuneParentId);
        await refresh();
      } catch (error) {
        setFineTuneStartError(String(error));
      } finally {
        setFineTuneStarting(false);
        setBusy(null);
      }
    },
    [api, fineTuneParentId, refreshFineTunePrograms, refresh, investigationEncounterId, workers, duty],
  );

  async function restoreFrozenStrategy() {
    const key = investigationTarget?.holderKey;
    if (!api?.restore_strategy || !key || busy) return;
    setBusy("Restoring frozen strategy");
    setActionError(null);
    try {
      const paused = await api.command("pause", {});
      if (paused?.ok === false) throw new Error(paused.error ?? "Could not pause search");
      const deadline = Date.now() + 60000;
      let snapshot = await api.status();
      while (snapshot.state === "Running" || snapshot.state === "Saving") {
        if (Date.now() > deadline) throw new Error("Battles are still saving. Wait for Paused, then restore this build again.");
        await new Promise(resolve => setTimeout(resolve, 500));
        snapshot = await api.status();
      }
      const restored = await api.restore_strategy(key);
      if (!restored.ok || !restored.candidateId) throw new Error(restored.error ?? "Build could not be restored");
      await refresh();
      await openInvestigation({candidateId: restored.candidateId});
      setDetailTab("ranges");
      setNotice("Frozen build restored. Choose a statistic and Start refinement to resume the worker pool.");
    } catch (error) { setActionError(String(error)); }
    finally { setBusy(null); }
  }

  /**
   * The frozen record behind a holder-scoped investigation, merged with the live status holder.
   *
   * The score basis is read from a real source and never guessed: an earned record's basis comes
   * from the retained run whose seed pair and digest are the holder's own, and otherwise from the
   * encounter's persisted lifetime stats; a potential record's basis only ever comes from those
   * stats, because a run row's own basis labels *earned* chests and would mislabel an opportunity.
   */
  const investigationHolder = useMemo(() => {
    const key = investigationTarget?.holderKey ?? null;
    const returned = (investigationDetail?.holder ?? null) as HolderInfo | null;
    if (!key && !returned) return null;
    const parts = (key ?? returned?.key ?? "").split(":");
    const metric = parts[0] ?? null;
    const fight = parts.length >= 3 ? `${parts[1]}:${parts[2]}` : null;
    const stored = key ? recordHolders[key] : undefined;
    const mathSeed = returned?.mathSeed ?? stored?.mathSeed ?? null;
    const libSeed = returned?.libSeed ?? stored?.libSeed ?? null;
    const digest = returned?.digest ?? stored?.digest ?? null;
    const matchingRun =
      typeof mathSeed === "number" && typeof libSeed === "number"
        ? ([...storedRuns, ...trialRuns].find((run) => {
            const seeds = runSeeds(run);
            return (
              seeds != null &&
              seeds[0] === mathSeed &&
              seeds[1] === libSeed &&
              (digest == null || run.digest == null || run.digest === digest)
            );
          }) ?? null)
        : null;
    const encounterBasis = fight
      ? (metric === "potential" ? encounterStats[fight]?.potentialBasis : encounterStats[fight]?.earnedBasis) ?? null
      : null;
    const basis = (metric === "earned" ? matchingRun?.basis : null) ?? encounterBasis;
    return {
      key: key ?? returned?.key ?? null,
      metric,
      fight,
      value: returned?.value ?? stored?.value ?? null,
      verdict: returned?.verdict ?? stored?.verdict ?? null,
      candidate: returned?.candidate ?? stored?.candidate ?? null,
      label: returned?.label ?? null,
      mathSeed,
      libSeed,
      digest,
      tickLimit: returned?.tickLimit ?? stored?.tickLimit ?? null,
      basis,
    };
  }, [investigationTarget, investigationDetail, recordHolders, encounterStats, storedRuns, trialRuns]);

  const investigationLabel =
    investigationDetail?.label ??
    investigationHolder?.label ??
    investigationCandidate?.label ??
    (investigationTarget?.candidateId
      ? shortId(investigationTarget.candidateId)
      : investigationTarget?.holderKey ?? "strategy");

  /** The seed pair the record reproduces on: the holder's own pair, else the first stored run's. */
  const investigationSeedPair = useMemo(() => {
    const holder = investigationHolder;
    if (holder && typeof holder.mathSeed === "number" && typeof holder.libSeed === "number") {
      return `[${holder.mathSeed}, ${holder.libSeed}]`;
    }
    for (const run of storedRuns) {
      const seeds = runSeeds(run);
      if (seeds) return `[${seeds.join(", ")}]`;
    }
    for (const run of trialRuns) {
      const seeds = runSeeds(run);
      if (seeds) return `[${seeds.join(", ")}]`;
    }
    return null;
  }, [investigationHolder, storedRuns, trialRuns]);

  /**
   * Fetch the host's ranked strategies for the focused encounter. The host owns the aggregation and
   * the comparability rule; this only sorts and renders what it returns.
   */
  const loadEncounterStrategies = useCallback(
    async (fight: string) => {
      const host = api;
      if (!host) return;
      const fetchStrategies = host.encounter_strategies;
      if (typeof fetchStrategies !== "function") {
        setEncounterStrategies(null);
        setStrategiesFight(null);
        setStrategiesError("this host build does not expose encounter_strategies yet");
        return;
      }
      const [encounterId, defeatCount] = fight.split(":");
      setStrategiesBusy(true);
      setStrategiesError(null);
      try {
        const result = await fetchStrategies(Number(encounterId), Number(defeatCount ?? 0) || 0);
        if (!result || result.ok === false) {
          setEncounterStrategies(null);
          setStrategiesFight(null);
          setStrategiesError(result?.error ?? "the host returned no encounter strategies");
          return;
        }
        setEncounterStrategies(result);
        setStrategiesFight(fight);
      } catch (error) {
        setStrategiesError(String(error));
        setStrategiesFight(null);
      } finally {
        setStrategiesBusy(false);
      }
    },
    [api],
  );

  useEffect(() => {
    if (!fightKey) {
      setEncounterStrategies(null);
      setStrategiesError(null);
      setStrategiesFight(null);
      return;
    }
    void loadEncounterStrategies(fightKey);
  }, [fightKey, loadEncounterStrategies]);

  /**
   * Open the investigation panel for one exact build: a live candidate by id, or a frozen record
   * holder by key, so a pruned candidate's scenario and seed pair stay reachable.
   *
   * The host resolves exactly one identity - a frozen record by its holder key, a live build by its
   * candidate id - and refuses a request that carries both. So the target is trimmed to a single
   * identity here, at the one funnel every panel action shares: the holder key wins when one is
   * present, because that is the frozen record the reader picked, and the candidate id is used
   * otherwise. The stored target is the trimmed one, so the panel's own replay and trial calls
   * inherit the same single identity instead of re-sending both.
   */
  const openInvestigation = useCallback(
    async (requested: InvestigationTarget) => {
      const host = api;
      const token = ++investigationToken.current;
      const target: InvestigationTarget = requested.holderKey
        ? { holderKey: requested.holderKey }
        : { candidateId: requested.candidateId ?? null };
      setInvestigationTarget(target);
      setWorkspaceView("strategy");
      setDetailTab("summary");
      setInvestigation(null);
      setInvestigationError(null);
      setTrialTotal(null);
      setTrialDone(0);
      setTrialError(null);
      setTrialRunning(false);
      setVisualRunning(false);
      setVisualError(null);
      setVisualNote(null);
      setLatestBatch(null);
      setLatestBatchBest(null);
      const fetchDetail = host?.strategy_detail;
      if (!host || typeof fetchDetail !== "function") {
        setInvestigationError("this host build does not expose strategy_detail yet");
        return;
      }
      setInvestigationBusy(true);
      try {
        const detail = await fetchDetail(target.candidateId ?? null, target.holderKey ?? null);
        if (token !== investigationToken.current) return;
        if (!detail || detail.ok === false) {
          setInvestigationError(detail?.error ?? "the host returned no strategy detail");
          return;
        }
        setInvestigation({ target, detail });
      } catch (error) {
        if (token === investigationToken.current) setInvestigationError(String(error));
      } finally {
        if (token === investigationToken.current) setInvestigationBusy(false);
      }
    },
    [api],
  );

  /**
   * A click on one of the four Encounter overview readings. It focuses that fight and points the
   * ranked list at the exact build the reading belongs to - the frozen record holder's candidate for
   * a lifetime maximum, the published candidate for an average - and deliberately does NOT open the
   * investigation panel. The panel opens only from the owning build's own name in the ranked list,
   * so the reader sees which build the number belongs to before going deep into it.
   */
  const focusEncounterMetric = useCallback(
    (encounterId: number, metric: OverviewMetricName) => {
      focusFight(encounterId);
      const fight = `${encounterId}:0`;
      /*
       * When the fight is already focused the fightKey effect will not fire, so re-read the ranked
       * list here: the row this reading owns is then resolved against a fresh payload, never a
       * possibly stale one from before a rebuild of the library.
       */
      if (fightKey === fight) void loadEncounterStrategies(fight);
      const stat = encounterStats[fight];
      const averages = averageLeadersByFight.get(fight) ?? null;
      if (metric === "highest-earned" || metric === "highest-potential") {
        const earned = metric === "highest-earned";
        const holderKey = `${earned ? "earned" : "potential"}:${encounterId}:0`;
        const value = earned ? stat?.highestChestsEarned ?? null : stat?.highestPotentialChests ?? null;
        const measuredValue = earned ? stat?.ea?.highestChestsEarned : stat?.ea?.highestPotentialChests;
        const measuredCandidate = earned ? stat?.ea?.earnedCandidateId : stat?.ea?.potentialCandidateId;
        const ownsCommunityReading = value != null && measuredValue === value && Boolean(measuredCandidate);
        setOverviewMetricFocus({
          nonce: ++overviewMetricNonce.current,
          metric,
          encounterId,
          candidateId: ownsCommunityReading ? measuredCandidate! : recordHolders[holderKey]?.candidate ?? null,
          holderKey: ownsCommunityReading ? null : holderKey,
          provenance: `${earned ? "Highest earned" : "Highest potential"}${
            value == null ? "" : ` ${formatInt(value)}`
          } · ${ownsCommunityReading ? "saved Community measurement" : "frozen record holder"}`,
        });
        return;
      }
      const earned = metric === "avg-earned";
      const leader = earned ? averages?.earned ?? null : averages?.potential ?? null;
      setOverviewMetricFocus({
        nonce: ++overviewMetricNonce.current,
        metric,
        encounterId,
        candidateId: leader?.candidateId ?? null,
        holderKey: null,
        provenance: leader
          ? `${earned ? "Avg earned" : "Avg potential"} ${formatNumber(leader.mean, 2)} over n=${formatInt(
              leader.samples,
            )} measured runs`
          : `${earned ? "Avg earned" : "Avg potential"} · measured mean`,
      });
    },
    [focusFight, fightKey, loadEncounterStrategies, encounterStats, averageLeadersByFight, recordHolders],
  );

  /** A ranked row: make it the page's selected strategy and open its investigation panel. */
  const selectEncounterStrategy = useCallback(
    (row: EncounterStrategyRow) => {
      /*
       * Opening the build the marked reading already owns keeps the mark, so the reader can see which
       * overview number brought them here while they read; choosing any other build clears it.
       */
      const isMetricOwner = overviewMetricOwner.row?.candidateId === row.candidateId;
      const holderKey = isMetricOwner ? overviewMetricFocus?.holderKey ?? null : null;
      if (!isMetricOwner) clearOverviewMetricFocus();
      setSelectedId(row.candidateId);
      setFamilyKey(null);
      setExampleLabel(null);
      /*
       * The reading's frozen record is addressed by its holder key exactly, never beside the
       * candidate id: a detail request that carries both is refused outright, which is what made this
       * click fall back to an error. Any other build is addressed by its candidate id alone.
       */
      void openInvestigation(holderKey ? { holderKey } : { candidateId: row.candidateId });
    },
    [openInvestigation, clearOverviewMetricFocus, overviewMetricFocus, overviewMetricOwner.row],
  );

  /**
   * A build name inside the search portfolio: the same select-and-scroll path as a ranked row, so a
   * lane member or a graduated build becomes the page's Selected strategy and its workspace heading
   * is brought into view. If it is also the row an overview reading owns, the reading's highlight is
   * kept; any other build clears it, exactly as choosing a ranked row does.
   */
  const openPortfolioBuild = useCallback(
    (candidateId: string, label: string | null) => {
      const isMetricOwner = overviewMetricOwner.row?.candidateId === candidateId;
      const holderKey = isMetricOwner ? overviewMetricFocus?.holderKey ?? null : null;
      if (!isMetricOwner) clearOverviewMetricFocus();
      setSelectedId(candidateId);
      setFamilyKey(null);
      setExampleLabel(label);
      void openInvestigation(holderKey ? { holderKey } : { candidateId });
    },
    [openInvestigation, clearOverviewMetricFocus, overviewMetricFocus, overviewMetricOwner.row],
  );

  /**
   * A pruned record holder's row: open its frozen scenario by holder key, so a record whose build no
   * longer exists stays fully inspectable. The holder key is the whole identity here.
   */
  const openOrphanHolder = useCallback(
    (holder: OrphanHolder) => {
      if (overviewMetricOwner.holder?.key !== holder.key) clearOverviewMetricFocus();
      setFamilyKey(null);
      setExampleLabel(null);
      void openInvestigation({ holderKey: holder.key });
    },
    [openInvestigation, clearOverviewMetricFocus, overviewMetricOwner.holder],
  );

  /**
   * One tested point of a fine-tuning program: open that exact child build's investigation panel so
   * its slots, equipment, skills and repeat simulation are all inspectable. The panel's own
   * `fineTuneChildOrigin` reads the host's program lineage to offer the route back to the parent.
   */
  const openFineTuneChild = useCallback(
    (child: { candidateId: string; label: string }) => {
      setSelectedId(child.candidateId);
      setFamilyKey(null);
      setExampleLabel(child.label);
      void openInvestigation({ candidateId: child.candidateId });
    },
    [openInvestigation],
  );

  /**
   * Open the build the host's automatic-tuning diagnostic selected as its parent, so a reader whose
   * average winner differs from the auto-tuned parent can jump straight to that build instead of
   * reading its ranges as if they belonged to the selected one.
   */
  const openAutoTuneParent = useCallback(
    (candidateId: string, label: string) => {
      setSelectedId(candidateId);
      setFamilyKey(null);
      setExampleLabel(label);
      void openInvestigation({ candidateId });
    },
    [openInvestigation],
  );

  const closeInvestigation = useCallback(() => {
    setWorkspaceView("strategies");
    investigationToken.current += 1;
    setInvestigationTarget(null);
    setInvestigation(null);
    setInvestigationError(null);
    setTrialTotal(null);
    setTrialDone(0);
    setTrialError(null);
    setTrialRunning(false);
    setVisualRunning(false);
    setVisualError(null);
    setVisualNote(null);
    setLatestBatch(null);
    setLatestBatchBest(null);
  }, []);

  /**
   * Run fresh seed trials in host chunks of at most eight, so 8/32/64 totals report progress and the
   * page is never blocked on one long call. Each reply is cumulative, and a reply is dropped if the
   * reader has already switched builds, so results stay attributable to exactly one strategy.
   *
   * The whole requested batch is aggregated as one batch: the host's own `batchSummary` from every
   * chunk call is merged, and the batch's new rows (this batch's slice of the cumulative trial bank)
   * supply the low reading and the spread. The card therefore reports the 8/32/64 the reader asked
   * for, not the running trial total.
   */
  const startExperiment = useCallback(async (count: number, mode = "evaluate") => {
    if (!api?.start_strategy_experiment || !investigationTarget || busy) return;
    setBusy("Starting focused experiment");
    setTrialError(null);
    setExperimentReport(null);
    try {
      const paused = await api.command("pause", {});
      if (!paused.ok) throw new Error(paused.error ?? "Pause refused");
      const deadline = Date.now() + 60000;
      let snapshot = await api.status();
      while (snapshot.state === "Running" || snapshot.state === "Saving") {
        if (Date.now() > deadline) throw new Error("Battles are still saving. Try again when Paused.");
        await new Promise(resolve => setTimeout(resolve, 500));
        snapshot = await api.status();
      }
      const result = await api.start_strategy_experiment(investigationTarget.candidateId ?? null,
        investigationTarget.holderKey ?? null, count, mode);
      if (!result.ok) throw new Error(result.error ?? "Experiment refused");
      const started = await api.command("start", {workers, duty});
      if (!started.ok) throw new Error(started.error ?? "Experiment saved; Start was refused");
      setNotice(mode === "skills" ? "Skill comparisons started. Each build receives the requested shared seed block; catch-up runs also count." : "Focused batch started. All workers test this build; every completed battle contributes to library evidence and totals.");
      await refresh();
    } catch (error) { setTrialError(String(error)); }
    finally { setBusy(null); }
  }, [api, investigationTarget, busy, workers, duty, refresh]);

  async function readExperimentReport() {
    try {
      const result = await api?.experiment_report?.();
      if (!result?.ok) throw new Error(result?.error ?? "Report unavailable");
      setExperimentReport(result.experiment ?? null);
    } catch (error) { setActionError(String(error)); }
  }

  async function endExperiment() {
    if (!api || busy) return;
    setBusy("Ending experiment");
    try {
      const result = await api.command("end_experiment", {});
      if (!result.ok) throw new Error(result.error ?? "End experiment refused");
      await refresh();
    } catch (error) { setActionError(String(error)); }
    finally { setBusy(null); }
  }

  const runFreshTrials = useCallback(
    async (total: number) => {
      if (api?.start_strategy_experiment) { await startExperiment(total); return; }
      if (total > 1024) { setTrialError("Restart the updated desktop host to use persistent worker batches."); return; }
      const host = api;
      const target = investigationTarget;
      const runStrategy = host?.run_strategy;
      if (!host || typeof runStrategy !== "function" || !target) return;
      if (target.candidateId == null && target.holderKey == null) return;
      if (trialRunning || visualRunning) return;
      const token = investigationToken.current;
      trialCancel.current = false;
      const completedRows: StrategyRun[] = [];
      const chunkSummaries: Array<HostBankSummary | null | undefined> = [];
      setTrialTotal(total);
      setTrialDone(0);
      setTrialError(null);
      setVisualError(null);
      setVisualNote(null);
      setTrialRunning(true);
      setLatestBatch(null);
      setLatestBatchBest(null);
      try {
        let done = 0;
        let latest = investigationDetail;
        while (done < total && !trialCancel.current) {
          if (token !== investigationToken.current) return;
          const chunk = Math.min(DISCOVERY_RUNS, total - done);
          const result = await runStrategy(target.candidateId ?? null, target.holderKey ?? null, chunk);
          if (token !== investigationToken.current) return;
          if (result && Array.isArray(result.trialRuns)) latest = result;
          if (!result || result.ok === false) {
            setTrialError(result?.error ?? "the host refused the trial batch");
            break;
          }
          chunkSummaries.push(result.batchSummary ?? null);
          completedRows.push(...(result.trialRuns ?? []).slice(-chunk));
          done += chunk;
          setTrialDone(Math.min(done, total));
          setInvestigation({ target, detail: result });
        }
        /*
         * Aggregate whatever this batch actually completed: a batch refused mid-way still reports
         * the fights it did record, and the comparison gate says the batch is short rather than
         * silently comparing a partial batch with the stored bank.
         */
        const rows = completedRows;
        setLatestBatchBest(
          rows.reduce<StrategyRun | null>((best, run) => {
            const value = runEarnedValue(run);
            if (value == null) return best;
            return !best || value > (runEarnedValue(best) ?? -1) ? run : best;
          }, null),
        );
        setLatestBatch(
          summarizeTrialBatch({
            requested: total,
            chunkSummaries,
            readings: rows.map(batchReading),
            storedSummary: latest?.storedSummary ?? null,
          }),
        );
      } catch (error) {
        if (token === investigationToken.current) setTrialError(String(error));
      } finally {
        if (token === investigationToken.current) setTrialRunning(false);
      }
    },
    [api, startExperiment, investigationTarget, investigationDetail, trialRunning, visualRunning],
  );

  /**
   * Run exactly one brand-new fight of the selected frozen build and show it.
   *
   * This is not the stored record's replay: the host draws a fresh seed pair the target has never
   * recorded, runs the canonical simulator once with a trace, records the outcome as one new
   * investigation trial, and returns the updated detail together with that trace. The reply is
   * dropped when the reader has switched builds (the investigation token), so a fight can never be
   * attributed to a different strategy, and the same path opens the returned trace in the shared
   * generated battle renderer, labelled with its own new seed pair and result.
   */
  const simulateStrategyVisual = useCallback(
    async (replayTarget?: InvestigationTarget) => {
      const host = api;
      const target = replayTarget ?? investigationTarget;
      const call = host?.simulate_strategy_visual;
      if (!host || typeof call !== "function" || !target) return;
      if (target.candidateId == null && target.holderKey == null) return;
      if (trialRunning || visualRunning || replayBusy) return;
      const token = investigationToken.current;
      if (!showReplay) replayReturnScroll.current = window.scrollY;
      setVisualRunning(true);
      setVisualError(null);
      setVisualNote(null);
      setReplayBusy(true);
      setReplayError(null);
      try {
        const result = await call(target.candidateId ?? null, target.holderKey ?? null);
        if (token !== investigationToken.current) return;
        if (!result || result.ok === false) {
          const message = result?.error ?? "the host refused the fresh visual simulation";
          setVisualError(message);
          setReplayError(message);
          return;
        }
        if (sameInvestigationTarget(target, investigationTarget)) setInvestigation({ target, detail: result });
        const seeds = runSeeds({ seeds: result.seeds ?? undefined });
        const title = `${result.label ?? investigationLabel} · new simulation${
          seeds ? ` · seeds [${seeds.join(", ")}]` : ""
        }`;
        if (!result.replay) {
          const message = "the host returned no trace for the fresh simulation";
          setVisualError(message);
          setReplayError(message);
          return;
        }
        const { result: parsed, issues } = parseBattleReplayResult(result.replay);
        if (!parsed) {
          const message = `replay payload rejected: ${issues.join("; ")}`;
          setVisualError(message);
          setReplayError(message);
          return;
        }
        // The new trial is the row the host just persisted for this fight's own seed pair, so its
        // outcome is read from the returned trials rather than re-derived on the page.
        const freshRow = (result.trialRuns ?? []).find((run) => {
          const rowSeeds = runSeeds(run);
          return Boolean(
            seeds && rowSeeds && rowSeeds[0] === seeds[0] && rowSeeds[1] === seeds[1],
          );
        });
        const outcome = freshRow ? RUN_OUTCOME_LABEL[runOutcome(freshRow)] : "recorded";
        setVisualNote(
          `New simulation, not a replay: one fresh fight on seeds [${seeds?.join(", ") ?? "unknown"}]` +
            ` (${outcome}) was recorded as a new trial for this build.`,
        );
        setReplayRecord(
          createGeneratedBattleRecord(
            parsed,
            result.visualSetup ?? undefined,
            [...REPLAY_WARNINGS, ...(result.warnings ?? [])],
            title,
          ),
        );
        setReplayTitle(title);
        setReplayContext({ detail: result, target, programs: replayTarget ? (replayContext?.programs ?? []) : fineTunePrograms });
        setShowReplay(true);
        window.scrollTo({ top: 0 });
      } catch (error) {
        if (token === investigationToken.current) {
          const message = String(error);
          setVisualError(message);
          setReplayError(message);
        }
      } finally {
        setVisualRunning(false);
        setReplayBusy(false);
      }
    },
    [api, investigationTarget, investigationLabel, trialRunning, visualRunning, replayBusy, fineTunePrograms, replayContext, showReplay],
  );

  /**
   * Whether the host can re-verify one recorded run for this target, and which control to call.
   *
   * The investigation run tables replay through `replay_strategy_run`, which resolves the target's
   * own frozen scenario and checks the rerun against the digest the record persisted. That works for
   * a live candidate and for a pruned holder alike, because the holder key carries its frozen
   * scenario; `replay` alone only ever knew the candidate's example seeds, which is why fresh trials
   * and pruned-holder rows used to fail.
   */
  const canReplayInvestigation =
    typeof api?.replay_strategy_run === "function" &&
    (investigationTarget?.candidateId != null || investigationTarget?.holderKey != null);

  const replayInvestigationRun = useCallback(
    async (run: StrategyRun) => {
      const host = api;
      const target = investigationTarget;
      const seeds = runSeeds(run);
      if (!host || typeof host.replay_strategy_run !== "function" || !target || !seeds) return;
      await presentReplay(
        () => host.replay_strategy_run!(target.candidateId ?? null, target.holderKey ?? null, seeds),
        `${investigationLabel} · seeds [${seeds.join(", ")}]`,
        target,
      );
    },
    [api, investigationTarget, investigationLabel, presentReplay],
  );

  /** Bring a freshly ranked fight into view rather than leaving its list off-screen. */
  useEffect(() => {
    if (!fightKey) return;
    const node = encounterStrategiesRef.current;
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [fightKey]);

  /**
   * Land on the workspace heading, not halfway down the pane.
   *
   * The selected strategy is opened by a click, and the blocks above it (the ranked list, the
   * encounter table) grow asynchronously afterwards; scrolling on the click alone therefore lands
   * mid-pane once that growth pushes the card down.
   *
   * It deliberately reads nothing from `overviewMetricOwner`: the marked row keeps its own highlight,
   * and that row's own scroll effect is guarded by its nonce marker, so it cannot re-assert itself
   * and pull the page back up after this scroll has taken the reader down to the workspace.
   */
  useEffect(() => {
    if (!investigationTarget) return;
    const node = investigationRef.current;
    if (!node || typeof node.scrollIntoView !== "function") return;
    const frame = requestAnimationFrame(() => {
      node.scrollIntoView({ behavior: "auto", block: "start" });
    });
    return () => cancelAnimationFrame(frame);
  }, [investigationTarget]);

  useEffect(() => {
    const bar = document.querySelector<HTMLElement>("[data-optimizer-run-bar]");
    const root = document.querySelector<HTMLElement>("[data-desktop-optimizer-root]");
    if (!bar || !root) return;
    const resize = new ResizeObserver(() => root.style.setProperty("--optimizer-toolbar-height", `${bar.getBoundingClientRect().height}px`));
    resize.observe(bar);
    return () => resize.disconnect();
  }, [showReplay]);
  /**
   * The host's read-only students history: counts, tiers and the rule-break ledger. Shared by the
   * historical view (kept inside collapsed read-only details). Nothing here is re-derived - it is the host's own history.
   */
  const studentHistoryBlock = (
    <>
{status?.students?.report ? (
  <ul className="space-y-0.5 text-[11px] tabular-nums" data-student-report>
    {Object.entries(status.students.report).map(([name, entry]) => (
      <li key={name}>
        <span className="font-medium">{name}</span> · candidates{" "}
        {entry.candidates ?? 0} · encounters {entry.encounters ?? 0} · runs{" "}
        {(entry.runs ?? 0).toLocaleString()}
      </li>
    ))}
  </ul>
) : (
  <p className="text-[11px] text-muted-foreground">
    Counts are read on demand. Refresh once the search has run for a while to see what
    each stream actually received.
  </p>
)}
{status?.students?.tiers?.length ? (
  <div className="space-y-1 border-t pt-3" data-student-tiers>
    <p className="text-[11px] font-medium">
      Rebellious cheater — one tier per distance from the community rules
    </p>
    {status.students.tiers.map((tier) => (
      <p key={tier.name} className="text-[11px] text-muted-foreground">
        <span className="font-medium text-foreground">{tier.name}</span> · breaks{" "}
        {tier.minimum === tier.maximum
          ? `${tier.minimum} ${tier.minimum === 1 ? "rule" : "rules"}`
          : `${tier.minimum} to ${tier.maximum} rules`}{" "}
        at a time · may break {tier.rules.join(", ")}
      </p>
    ))}
    <p className="text-[11px] text-muted-foreground">
      C6 is the herb rule, and the herb branch does not exist yet, so no tier can break
      it until the new library is in use.
    </p>
  </div>
) : null}
{status?.students?.ledger ? (
  <div className="space-y-1 border-t pt-3" data-student-ledger>
    <p className="text-[11px] font-medium">
      Rule-break ledger — what each version actually broke, and what it measured
    </p>
    {Object.entries(status.students.ledger).length === 0 ? (
      <p className="text-[11px] text-muted-foreground">
        No rebel has run yet. Each line will read as tier.version, the exact set of
        rules it broke, and the chests and runs that combination measured — so
        &ldquo;breaking C4 cost this much&rdquo; is answered rather than guessed.
      </p>
    ) : (
      Object.entries(status.students.ledger).map(([version, sets]) => (
        <p key={version} className="text-[11px] tabular-nums">
          <span className="font-medium">{version}</span> ·{" "}
          {Object.entries(sets)
            .map(
              ([rules, entry]) =>
                `broke ${rules} → ${
                  entry.bestMeanChests === undefined
                    ? "nothing measured yet"
                    : `${entry.bestMeanChests} mean chests`
                } (${(entry.runs ?? 0).toLocaleString()} runs)`,
            )
            .join(" | ")}
        </p>
      ))
    )}
  </div>
) : null}
    </>
  );

  const stateLabel = status?.state ?? (api ? "Waiting for the host" : "Host not connected");
  /** The open library, named by its basename; the host publishes the full path. */
  const libraryName = (() => {
    const value = status?.library ?? "";
    if (!value) return "—";
    const parts = value.split(/[\\/]/);
    return parts[parts.length - 1] || value;
  })();
  /**
   * Why this library cannot simply run under the current generator, if it cannot: a changed engine
   * (the host reports the library as incompatible) or an older search-space contract. Nothing is
   * shown when neither applies, so the toolbar stays quiet about libraries that are current.
   */
  const libraryNotice = (() => {
    if (!status) return null;
    if (status.compatible === false) return "engine changed";
    const version = status.searchSpaceVersion;
    if (typeof version === "number" && version < SEARCH_SPACE_VERSION) return `search space v${version}`;
    return null;
  })();
  /** Time per completed battle, from the runner's own timing of every timed run. */
  /**
   * Throughput, read from the host's own measurement.
   *
   * The app must not present three numbers that cannot all be true at once, so every figure here
   * comes from one place: `secondsPerRun` is the runner's mean engine time per battle, and
   * `battlesPerSecond` is the host's measured aggregate over a rolling window of completed battles
 * across every worker - including start-up and idle, because that is what is actually achieved.
 * `battlesPerHour` is that measurement times 3600; it is never a separate projection. Capacity is
 * reported beside it so the shortfall between "what the workers could sustain" and "what the
 * search is achieving" is visible instead of hidden.
 */
  const throughput = status?.throughput ?? null;
  /*
   * The MP-recovery allowance boxes are seeded from the host and re-seeded only when the host's own
   * values change - keyed on their serialisation, not on the payload object, so a two-second status
   * poll cannot wipe a half-typed quantity out of the form.
   */
  const mpRecoverySetting = status?.mpRecovery?.setting ?? null;
  const mpRecoverySettingKey = mpRecoverySetting ? JSON.stringify(mpRecoverySetting) : null;
  useEffect(() => {
    if (!mpRecoverySetting) return;
    setMpRecoveryDraft({
      enabled: mpRecoverySetting.enabled === true,
      stock: Number(mpRecoverySetting.stock ?? 0),
      maxUses: Number(mpRecoverySetting.maxUses ?? 0),
      bank: Number(mpRecoverySetting.bank ?? 64),
      maxChildren: Number(mpRecoverySetting.maxChildren ?? 8),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the serialised values on purpose
  }, [mpRecoverySettingKey]);
  const secondsPerRun = throughput?.secondsPerRun ?? status?.averageSimulationSeconds ?? null;
  const activeWorkers = throughput?.effectiveBattleWorkers ?? throughput?.workers ?? workers;
  const capacityWorkers = throughput?.capacityWorkers ?? activeWorkers;
  // Direct worker activity from the coordinator's task lifetimes: how many workers are running a
  // battle right now, the 10 s time-weighted average, and that average as a share of the configured
  // pool. This is the honest occupancy reading - `utilization` below is achieved/capacity, which
  // depends on the capacity arithmetic and can disagree with it by a large factor.
  const coordinator = status?.scheduler ?? null;
  const executionQueue = coordinator?.executionQueue ?? null;
  const nativeProcess = coordinator?.nativeProcess ?? null;
  const nativeCounters = nativeProcess?.workCounters ?? null;
  const configuredWorkers = coordinator?.workers ?? workers ?? 0;
  const outstandingJobsNow = executionQueue?.inflight ?? nativeProcess?.acceptedCommands ??
    coordinator?.activeWorkers ?? coordinator?.busy ?? null;
  const averageWorkers = coordinator?.activeWorkersAverage10s ?? null;
  const battlesPerSecond = throughput?.battlesPerSecond ?? null;
  const battlesPerHour = throughput?.battlesPerHour ?? null;
  const capacity = throughput?.capacityBattlesPerSecond ?? null;
  const rateWindow = throughput?.windowSeconds ?? null;
  const nativeCpuUtilization = throughput?.cpuUtilization ?? null;
  const processCpuPerRun = throughput?.secondsPerRunBasis?.startsWith("native-process CPU") === true;
  const nativeWorkDetails = nativeCounters
    ? nativeCounters.executingWorkers != null
      ? `workers ${formatInt(nativeCounters.executingWorkers)} executing · ${formatInt(nativeCounters.readyQueueDepth)} queued · ${formatInt(nativeCounters.acceptedOutstanding)} accepted outstanding · ${formatInt(nativeCounters.completedUnharvested)} awaiting harvest · ${formatInt(nativeCounters.pendingDurableResults)} pending durable save · ${formatInt(nativeCounters.nativeInFlight)} native calls in flight · ${formatInt(nativeCounters.dispatchPending)} dispatch pending · ${formatInt(nativeCounters.nativeSuccessfulBattles)} successful · ${formatInt(nativeCounters.unsavedSuccessfulResults)} successful not yet durable · mean job ${nativeCounters.meanWorkerJobSeconds == null ? "—" : `${nativeCounters.meanWorkerJobSeconds.toFixed(3)} s`} · summed worker busy ${nativeCounters.nativeWorkerBusySeconds == null ? "—" : `${nativeCounters.nativeWorkerBusySeconds.toFixed(1)} s`} · native call wall ${nativeCounters.nativeCallSeconds == null ? "—" : `${nativeCounters.nativeCallSeconds.toFixed(1)} s`}`
      : nativeCounters.acceptedOutstandingTrials != null
        ? `${formatInt(nativeCounters.acceptedOutstandingTrials)} accepted outstanding · ${formatInt(nativeCounters.uncommittedCompletedTrials)} completed awaiting commit · ${formatInt(nativeCounters.remainingUnsubmittedTrialSlots)} slots unsubmitted`
        : nativeCounters.workTelemetry
          ? `${formatInt(nativeCounters.workTelemetry.accepted)} accepted · ${formatInt(nativeCounters.workTelemetry.queued)} queued · ${formatInt(nativeCounters.workTelemetry.active)} active · ${formatInt(nativeCounters.workTelemetry.pendingSave)} pending save · ${formatInt(nativeCounters.workTelemetry.durable)} durable`
          : ""
    : "";
  return (
    <div
      className="min-h-screen bg-background text-foreground"
      data-desktop-optimizer-root
      data-workspace-view={workspaceView}
      data-optimizer-state={status?.state ?? "unavailable"}
    >
      <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-6">
        {showReplay ? (
          <div className="space-y-3" data-optimizer-replay>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => { setShowReplay(false); requestAnimationFrame(() => window.scrollTo({ top: replayReturnScroll.current })); }}
                data-action="back-to-optimizer"
              >
                <ChevronLeft className="mr-1 h-3.5 w-3.5" /> Back to optimiser
              </Button>
              <span className="text-xs text-muted-foreground" data-optimizer-replay-title>
                {replayTitle ?? "selected trace"}
              </span>
              <Badge variant="secondary" className="text-[10px]">
                read-only · branch interactions disabled
              </Badge>
            </div>
            {visualNote ? (
              <p className="text-[11px] text-muted-foreground" data-visual-trace-note>
                {visualNote}
              </p>
            ) : null}
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="min-w-0"><GeneratedBattleReplay initialRecord={replayRecord} embedded /></div>
              <aside className="min-w-0 space-y-3" data-replay-strategy-context>
                {replayContext ? <>
                  <div className="rounded-xl border p-4"><h2 className="font-semibold">{replayContext.detail.label ?? "Recorded strategy"}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">Exact build used for this battle. Ranges below describe measured family experiments, not changes applied to this replay.</p>
                    <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" disabled={replayBusy || simulationRunning} onClick={() => void simulateStrategyVisual(replayContext.target)}>Try new RNG & watch</Button>
                    <Button variant="outline" size="sm" onClick={() => { setShowReplay(false); void openInvestigation(replayContext.target); }}>Open strategy · test or refine</Button></div>
                    {visualRunning ? <p className="mt-2 text-xs" role="status">Simulating a new fight…</p> : null}
                    {visualError ? <p className="mt-2 text-xs text-destructive" role="alert">{visualError}</p> : null}
                  </div>
                  <StrategyFormationPanel slots={teamBuildSlots(replayContext.detail.scenario)} formation={replayContext.detail.formation} />
                  <RangeSummary programs={replayContext.programs} />
                </> : <p className="text-sm text-muted-foreground">Build context is loading or unavailable. Return to the strategy to inspect its recorded loadout.</p>}
              </aside>
            </div>
          </div>
        ) : (
          <>
            <PageHeader
              icon={<Swords className="h-5 w-5" />}
              title="Strategy Optimiser"
              actions={
                <div className="flex flex-wrap items-center gap-2">
                  <ToneBadge category={stateCategory(stateLabel)}>{stateLabel}</ToneBadge>
                </div>
              }
            >
              <p>Find promising builds, understand their results, and improve them with measured experiments.</p>
            </PageHeader>

            {/*
              Transport controls stay pinned to the top of the page: they are the thing you reach for
              while reading results, and the results area is long. The worker count and New library
              live here as well, so every primary control has exactly one home; the duty-cycle
              slider and the import/export/expand actions stay in the Run control card below.
            */}
            <div
              className="sticky top-0 z-20 -mx-4 space-y-1 border-b border-border/60 bg-background/95 px-4 py-2 backdrop-blur supports-[backdrop-filter]:bg-background/80"
              data-optimizer-run-bar
            >
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <ToneBadge category={stateCategory(stateLabel)}>{stateLabel}</ToneBadge>
                <span
                  className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground"
                  title={status?.library ? `Open library: ${status.library}` : "No library is open yet."}
                >
                  <FolderOpen className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span className="max-w-[18rem] truncate font-mono text-foreground" data-optimizer-library-name>
                    {libraryName}
                  </span>
                  {libraryNotice ? (
                    <span data-optimizer-library-status>
                      <ToneBadge category="warning">{libraryNotice}</ToneBadge>
                    </span>
                  ) : null}
                </span>
                <span className="hidden h-4 w-px bg-border sm:block" aria-hidden="true" />
                <Select value={String(workers)} onValueChange={(value) => setWorkers(Number(value))} disabled={!api}>
                  <SelectTrigger className="h-8 w-[4.5rem] text-xs" data-optimizer-workers aria-label="Workers">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {allowedWorkers.map((count) => (
                      <SelectItem key={count} value={String(count)}>
                        {count}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => void sendCommand("Start", "community_start", { workers, duty, newCampaign: true })}
                  disabled={!api || busy !== null || status?.state === "Running" || status?.state === "Saving"}
                  data-action="optimizer-start"
                  data-optimizer-start-mode="community"
                  title="Search all encounters automatically. The host sets up and resumes the Community campaign; no per-encounter selection is needed."
                >
                  <Play className="mr-1 h-3.5 w-3.5" /> Run
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void sendCommand("Pause", "pause", {})}
                  disabled={!api || busy !== null || status?.state !== "Running"}
                  data-action="optimizer-pause"
                >
                  <Pause className="mr-1 h-3.5 w-3.5" /> Pause
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void sendCommand("Stop", "stop", {})}
                  disabled={!api || busy !== null || !status}
                  data-action="optimizer-stop"
                >
                  <Square className="mr-1 h-3.5 w-3.5" /> Stop
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void newLibrary()}
                  disabled={!api || busy !== null}
                  data-action="optimizer-new-library"
                  title="Create a new library. The save dialog offers the next free name; if you pick a file that already exists the host opens it, it never replaces it."
                >
                  <FilePlus2 className="mr-1 h-3.5 w-3.5" /> New library
                </Button>
                {/* An existing library is opened here, and only here: no save dialog, so the
                    operating system never asks about replacing it. Shown only when the host build
                    actually exposes the action, so an older host is not offered a dead control. */}
                {api?.open_library ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void openLibrary()}
                    disabled={!api || busy !== null}
                    data-action="optimizer-open-library"
                    title="Open an existing optimiser library file (strategiesv*.sqlite). Opens it in place; nothing is replaced or deleted."
                  >
                    <FolderOpen className="mr-1 h-3.5 w-3.5" /> Open library
                  </Button>
                ) : null}
                {/*
                  On-demand diagnostic export: a save dialog the host owns, a bounded reader that runs
                  in the background, and inline progress beside the button. It is read-only and allowed
                  while a search is running; it changes no setting and runs no battle.
                */}
                {api?.export_diagnostics ? (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setExportOpen((open) => !open);
                        setExportError(null);
                      }}
                      disabled={!api || exportStarting}
                      data-action="optimizer-export-diagnostics"
                      title="Save a detailed, shareable log of the most recent battles. Read-only: it runs no battle and changes nothing."
                    >
                      <Download className="mr-1 h-3.5 w-3.5" /> Export diagnostics
                    </Button>
                    {exportOpen ? (
                      <span
                        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border/70 bg-muted/30 px-2 py-1"
                        data-optimizer-export-controls
                      >
                        <label className="text-[11px] text-muted-foreground" htmlFor="optimizer-export-count">
                          Last battles
                        </label>
                        <Input
                          id="optimizer-export-count"
                          className="h-8 w-28 text-xs"
                          inputMode="numeric"
                          value={exportCountInput}
                          onChange={(event) => setExportCountInput(event.target.value)}
                          onBlur={() => {
                            const draft = exportCountInput.trim();
                            if (draft === "") return;
                            const parsed = Number(draft);
                            if (!Number.isInteger(parsed) || parsed < 1) setExportCountInput("1");
                            else if (parsed > DIAGNOSTIC_EXPORT_MAX) setExportCountInput(String(DIAGNOSTIC_EXPORT_MAX));
                          }}
                          disabled={exportStarting || exportRunning}
                          data-optimizer-export-count
                          aria-label="Number of most recent battles to include"
                        />
                        <span className="max-w-md text-[11px] text-muted-foreground">
                          1&ndash;{formatInt(DIAGNOSTIC_EXPORT_MAX)} most recent battles &middot; full results, changes
                          and diagnostics &middot; replay traces not included &middot; up to {DIAGNOSTIC_EXPORT_CAP_LABEL}
                        </span>
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => void startDiagnosticExport()}
                          disabled={!api || exportStarting || exportRunning || exportCountInput.trim() === ""}
                          data-action="optimizer-export-diagnostics-save"
                          title="Choose where to save the diagnostics file, then export in the background."
                        >
                          {exportStarting ? "Opening..." : "Save..."}
                        </Button>
                      </span>
                    ) : null}
                    {exportStatus ? (
                      <span
                        className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground"
                        data-optimizer-export-status
                      >
                        <span className="font-medium text-foreground" data-optimizer-export-stage>
                          {exportStatus.state === "done"
                            ? "Exported"
                            : exportStatus.state === "error"
                              ? "Export failed"
                              : (exportStatus.stageLabel ?? "Exporting")}
                        </span>
                        {exportRunning ? (
                          <>
                            <span>
                              {formatInt(exportStatus.included ?? 0)}
                              {exportStatus.requested ? ` / ${formatInt(exportStatus.requested)}` : ""} battles
                            </span>
                            {exportStatus.elapsedSeconds != null ? <span>{formatDuration(exportStatus.elapsedSeconds)}</span> : null}
                            {exportStatus.ratePerSecond ? <span>{exportStatus.ratePerSecond.toFixed(1)}/s</span> : null}
                            {exportStatus.etaSeconds != null ? <span>~{formatDuration(exportStatus.etaSeconds)} left</span> : null}
                          </>
                        ) : null}
                        {exportStatus.state === "done" ? (
                          <span>
                            {formatInt(exportStatus.included ?? 0)} battle(s)
                            {exportStatus.truncated ? `, ${formatInt(exportStatus.truncated)} truncated` : ""}
                            {exportStatus.path ? <span className="font-mono text-foreground"> {exportStatus.path.split(/[\\/]/).pop()}</span> : null}
                          </span>
                        ) : null}
                        {exportStatus.bytesWritten ? <span>{formatBytes(exportStatus.bytesWritten)}</span> : null}
                      </span>
                    ) : null}
                    {exportError ? (
                      <span className="text-[11px] text-destructive" data-optimizer-export-error>{exportError}</span>
                    ) : null}
                  </>
                ) : null}
                {busy ? <span className="text-[11px] text-muted-foreground">{busy}…</span> : null}
                {/*
                 * One identifier for "can I start something new right now". BUSY means an action is in
                 * flight or the search is draining a batch, so nothing new can be submitted until it
                 * finishes; READY means the controls are live. Running counts as READY because Pause and
                 * Stop are valid then — the badge answers "can I act", not "is the pool idle".
                 */}
                {status ? (
                  (() => {
                    const waiting = busy !== null || status.state === "Saving";
                    return (
                      <span
                        className={
                          "ml-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide " +
                          (waiting
                            ? "border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                            : "border-emerald-500/50 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400")
                        }
                        title={
                          waiting
                            ? "An action is in flight or the batch is saving; wait for it to finish."
                            : "The controls are live: you can start, pause, stop or change settings."
                        }
                        data-optimizer-readiness={waiting ? "busy" : "ready"}
                      >
                        {waiting ? "Busy" : "Ready"}
                      </span>
                    );
                  })()
                ) : null}
              </div>
              <p className="text-[11px] text-muted-foreground" data-optimizer-start-hint>
                  {searchFocusIds.length ? `Search ${searchFocusIds.length} selected encounter${searchFocusIds.length === 1 ? "" : "s"}. Clear Focus to search all.` : "Search all encounters automatically. Use the Focus toggles below to select any subset."}
                </p>
              {status?.campaign?.status === "complete" ? (
                <p className="text-xs text-muted-foreground" role="status">
                  Campaign finished: {status.campaign.completed ?? 0} of {status.campaign.total ?? 0} encounters checked.
                  {" "}Results are saved. Press Run to start another search budget.
                </p>
              ) : null}
              {/*
                A focused search with free workers and nothing to dispatch reports why; without this
                the flattened attempt counter looks identical to a slow but healthy run, which is the
                exact misreading it exists to prevent. The host's own sentence is shown, beside the
                fight's recovered name, in the pinned control bar - never a locally invented caveat.
              */}
              {focusedSearchStalled ? (
                <div
                  role="status"
                  className="flex items-start gap-2 rounded-md border border-orange-500/40 bg-orange-500/5 px-2.5 py-1.5"
                  data-optimizer-idle-warning
                >
                  <AlertTriangle
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-orange-600 dark:text-orange-400"
                    aria-hidden="true"
                  />
                  <p className="min-w-0 text-[11px] leading-snug">
                    <span className="font-semibold text-foreground" data-optimizer-idle-title>
                      {`Focused search has nothing to run${searchFocusLabel ? ` \u2014 ${searchFocusLabel}` : ""}.`}
                    </span>{" "}
                    <span className="text-muted-foreground" data-optimizer-idle-reason-text>
                      {searchIdleReason}
                    </span>
                  </p>
                </div>
              ) : null}
              {/*
                The runtime readings are the first thing read on this page, so they get real boxes
                rather than a line of 11px text. Every number still comes from the host's own
                measurement; the boxes only present it.
              */}
              <details data-performance-details>
                <summary className="cursor-pointer py-1 text-xs text-muted-foreground">{formatNumber(battlesPerSecond, 1)} battles/s · {configuredWorkers} workers · {formatInt(status?.totalRuns)} total runs <span className="ml-2">Search: {searchFocusLabel ?? "all encounters"}</span><span className="ml-2 underline underline-offset-2">Performance details</span></summary>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8" data-optimizer-perf-boxes>
                <PerfTile
                  hook="data-optimizer-rate-runs-per-second"
                  label="Actual speed"
                  value={battlesPerSecond == null ? "—" : `${battlesPerSecond.toFixed(2)}/s`}
                  hint={
                    battlesPerSecond == null
                      ? "battles/s · building a window"
                      : `battles/s · ${formatInt(battlesPerHour)} battles/hour`
                  }
                  title={throughput?.rateBasis ??
                    `Measured: battles finished per second from the execution pool (${activeWorkers} workers), over the last ${rateWindow == null ? "—" : rateWindow.toFixed(0)} seconds of completed battles.`}
                />
                <PerfTile
                  hook="data-optimizer-rate-capacity"
                  label="Capacity"
                  value={capacity == null
                    ? nativeProcess ? `${capacityWorkers} executors` : "—"
                    : `${capacity.toFixed(2)}/s`}
                  hint={capacity == null
                    ? nativeProcess ? "configured native executors; rate ceiling not measured" : "battles/s if never stalled"
                    : "battles/s if never stalled"}
                  title={capacity == null && nativeProcess
                    ? "The selected native build reports its configured executor count but does not expose a comparable single-worker simulation time, so a battles-per-second ceiling is unknown."
                    : `What ${capacityWorkers} execution workers at the configured duty cycle could sustain if the coordinator never stalled. The gap to the measured rate reflects time outside the worker's measured simulation call.`}
                />
                <PerfTile
                  hook="data-optimizer-perf-utilization"
                  label={nativeProcess ? "Process CPU" : "Utilization"}
                  value={nativeProcess
                    ? nativeCpuUtilization == null ? "Unmeasured" : formatPercent(nativeCpuUtilization)
                    : throughput?.achievedFraction == null ? "—" : formatPercent(throughput.achievedFraction)}
                  hint={nativeProcess ? "native child / host logical CPU" : "achieving % of capacity"}
                  title={nativeProcess
                    ? throughput?.cpuUtilizationBasis ?? "Native child CPU time divided by host logical CPU capacity."
                    : "Measured battles/second as a share of capacity."}
                />
                <PerfTile
                  hook="data-optimizer-workers-in-use"
                  label={nativeProcess ? "Native work" : "Accepted work"}
                  value={nativeProcess
                    ? `${nativeProcess.active ? "Active" : "Idle"} · ${nativeProcess.stage ?? "unknown"}`
                    : `In flight: ${formatInt(outstandingJobsNow ?? 0)}${executionQueue?.windowCapacity == null ? "" : ` / ${formatInt(executionQueue.windowCapacity)}`}`}
                  hint={nativeProcess
                    ? `${formatInt(nativeProcess.acceptedCommands ?? 0)} process command · ${formatInt(nativeProcess.durableJournalResults ?? 0)} durable · ${formatInt(nativeProcess.importedJournalResults ?? 0)} imported${nativeProcess.pendingImportResults ? ` · ${formatInt(nativeProcess.pendingImportResults)} awaiting import` : ""}${nativeWorkDetails ? ` · ${nativeProcess.active ? "current" : "last"} child: ${nativeWorkDetails}` : ""}`
                    : `10s avg outstanding: ${averageWorkers == null ? "—" : averageWorkers.toFixed(1)}`}
                  title={nativeProcess
                    ? `One native process command is accepted by the host. Child counters are native-reported with distinct queue/commit meanings; worker busy and native-call values are elapsed-work sums, not CPU. Configured executors: ${nativeProcess.configuredExecutors ?? configuredWorkers}. ${nativeCounters ? JSON.stringify(nativeCounters) : "No child work counters have been published yet."}`
                    : "Accepted work counts reserved jobs across the queue, active workers, and completed results waiting for harvest/save. The 10-second occupancy reading is based on accepted task lifetimes; it does not show how many workers are currently handling requests or CPU utilization."}
                />
                {executionQueue ? (
                  <PerfTile
                    hook="data-optimizer-execution-queue"
                    label="Worker pool"
                    value={`Handling: ${formatInt(executionQueue.executingWorkers ?? 0)} / ${formatInt(executionQueue.workers ?? configuredWorkers)}`}
                    hint={`Queued: ${formatInt(executionQueue.readyQueueDepth ?? 0)} · Cooling: ${formatInt(executionQueue.coolingWorkers ?? 0)} · Free: ${formatInt(executionQueue.availableWorkers ?? 0)}`}
                    title={`Workers handling a submitted pool request. This lifetime includes worker request processing, simulation, and response work; it is not a native-kernel-only or CPU-usage measurement. ${formatInt(executionQueue.completedUnharvested ?? 0)} completed result(s) are awaiting coordinator harvest and save.`}
                  />
                ) : null}
                <PerfTile
                  hook="data-optimizer-elapsed"
                  label="Elapsed"
                  value={formatElapsed(status?.sessionElapsedSeconds)}
                  hint={nativeProcess ? "active Running and Saving time" : "active search time"}
                  title={nativeProcess
                    ? "Coordinator monotonic wall time while Running or Saving. Paused and Stopped stretches do not accumulate."
                    : "Active search time for this Start session, taken from the coordinator's own clock. Paused stretches do not accumulate, and a poll or re-render never changes it."}
                />
                <PerfTile
                  hook="data-optimizer-rate-session-runs"
                  label="Runs this session"
                  value={formatInt(status?.sessionRuns)}
                  hint="since this Start"
                  title="Runs recorded since this Start. A new Start begins a new session and restarts this count at zero."
                />
                <PerfTile
                  hook="data-optimizer-rate-total-runs"
                  label="Total runs"
                  value={formatInt(status?.totalRuns)}
                  hint="this library, all sessions"
                  title="Every run this library has ever recorded, across all sessions."
                />
              </div>
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[11px] tabular-nums text-muted-foreground">
                {status?.scheduler?.communityProposalSeconds != null ? (
                  <span data-optimizer-proposal-cost title="Cumulative time spent generating candidate proposals in the active Community search">
                    Community proposal prep <span className="text-foreground">{status.scheduler.communityProposalSeconds.toFixed(1)}s</span>
                    {status.scheduler.communityPreparation?.reason ? ` · ${status.scheduler.communityPreparation.reason}` : ""}
                    {status.scheduler.communityPreparation?.workers ? ` · ${status.scheduler.communityPreparation.workers} prep workers` : ""}
                  </span>
                ) : null}
                <span
                  data-optimizer-rate-seconds-per-run
                  title={nativeProcess
                    ? processCpuPerRun
                      ? "Measured native child-process CPU seconds divided by durable native journal results. This includes native preparation, search, and saving work; it is not kernel-only simulation time."
                      : "The selected native process has not supplied enough CPU and durable-result measurements for a per-result figure. Kernel-only engine time is unavailable from this build."
                    : "Mean engine time for one completed battle, measured by the runner. Duty-cycle idle and IPC are excluded, so this is not the same thing as the aggregate rate."}
                >
                  {nativeProcess ? "native CPU / run" : "engine time"}{" "}
                  <span className="text-foreground">
                    {secondsPerRun == null ? "—" : secondsPerRun.toFixed(2)}
                  </span>{" "}
                  {nativeProcess ? "s per durable result" : "s per battle (one worker)"}
                </span>
                <span
                  data-optimizer-rate-runs-per-hour
                  title="The same measurement multiplied by 3600 - not an independent projection."
                >
                  <span className="text-foreground">{formatInt(battlesPerHour)}</span> battles/hour measured
                </span>
                <span data-optimizer-current-batch>
                  current batch{" "}
                  <span className="text-foreground">
                    {status?.currentRunElapsedSeconds == null
                      ? "idle"
                      : `${status.currentRunElapsedSeconds.toFixed(1)} s`}
                  </span>
                </span>
                <span data-optimizer-run-bar-live>
                  {status?.state === "Running" ? "running" : "not running"}
                </span>
              </div>
              </details>
              <nav aria-label="Optimizer workspace" className="flex gap-1 overflow-x-auto pt-1" data-workspace-nav>
                {([["overview", "Overview"], ["strategies", "Strategies"], ["strategy", "Selected strategy"], ["guide", "Strategy guide"], ["settings", "Search & settings"]] as const).map(([view, label]) =>
                  <Button key={view} size="sm" variant={workspaceView === view ? "default" : "ghost"} aria-current={workspaceView === view ? "page" : undefined} disabled={view === "strategy" && !investigationTarget} onClick={() => { setWorkspaceView(view); window.scrollTo({ top: 0 }); }} data-workspace-link={view}>{label}</Button>)}
              </nav>
            </div>

            {workspaceView === "guide" ? (
              <section className="space-y-4" data-strategy-guide>
                <div className="flex flex-wrap items-center gap-3">
                  <Label htmlFor="guide-encounter">Results for</Label>
                  <Select value={String(guideScope)} onValueChange={value => setGuideEncounter(Number(value))}>
                    <SelectTrigger id="guide-encounter" className="w-80"><SelectValue /></SelectTrigger>
                    <SelectContent>{encounterChoices.map(choice => <SelectItem key={choice.id} value={String(choice.id)}>{choice.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <OptimizerPlayerRegions available={typeof api?.encounter_player_regions === "function"}
                  loadSummary={loadGuide} scopeKey={`${status?.library ?? ""}:${guideScope}`}
                  scopeLabel={encounterChoices.find(choice => choice.id === guideScope)?.label ?? `Encounter ${guideScope}`}
                  autoLoad />
              </section>
            ) : null}

            {status?.focusedExperiment ? <Card data-focused-experiment className="min-w-0">
              <CardContent className="space-y-3 pt-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div><p className="font-semibold">{status.focusedExperiment.mode === "skills" ? "Controlled skill tests" : "Focused batch"} · {status.focusedExperiment.status}</p>
                    <p className="text-xs text-muted-foreground">{formatInt(status.focusedExperiment.completed)} / {formatInt(status.focusedExperiment.total)} new battles saved · {formatInt(status.focusedExperiment.requestedPerBuild)} requested per build. Uses your worker count. Pause / Start resumes the saved job.</p></div>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={() => void readExperimentReport()}>Read experiment results</Button>
                    {status.focusedExperiment.status === "active" ? <Button size="sm" variant="outline" disabled={Boolean(busy) || status.state === "Running" || status.state === "Saving" || encounterAwareActive} onClick={() => void endExperiment()}>End experiment</Button> : null}
                  </div>
                </div>
                <Progress value={status.focusedExperiment.total ? 100 * status.focusedExperiment.completed / status.focusedExperiment.total : 0} />
                {experimentReport?.id === status.focusedExperiment.id && experimentReport.comparisons ? <>
                  <p className="text-xs text-muted-foreground">Fresh shared seeds only. Delta is changed build minus original; negative means the removal hurt. Intervals describe this build and encounter, not a universal skill rule. Multiple comparisons and repeated inspection need independent confirmation. Uncertain does not mean unimportant.</p>
                  <div className="max-h-80 overflow-auto"><Table><TableHeader><TableRow><TableHead>Controlled change</TableHead><TableHead>Runs</TableHead><TableHead>Mean chests</TableHead><TableHead>Paired delta · approximate 95% interval</TableHead></TableRow></TableHeader><TableBody>
                    {experimentReport.comparisons.map(arm => <TableRow key={arm.candidateId}><TableCell><button className="text-left text-primary underline" onClick={() => openInvestigation({candidateId: arm.candidateId})}>{arm.label}</button></TableCell><TableCell>{formatInt(arm.runs)}</TableCell><TableCell>{formatMetric(arm.meanEarned)}</TableCell><TableCell>{arm.paired ? `${formatMetric(arm.paired.mean)} [${formatMetric(arm.paired.lower)}, ${formatMetric(arm.paired.upper)}] · n=${arm.paired.n}` : "Awaiting paired evidence"}</TableCell></TableRow>)}
                  </TableBody></Table></div>
                </> : null}
              </CardContent>
            </Card> : null}

            {/*
              The five battle lines, four difficulties each. This is the high-level overview: an
              aggregate per fight, not a run list. Every number comes from the host's own store, so
              the page is not re-deriving a maximum from a partial sample of runs.
            */}
            <Card className="min-w-0" data-optimizer-encounters>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Swords className="h-4 w-4" /> Encounter overview
                  <HelpTip label="How the encounter overview numbers are read">
                    <span className="block">
                      <span className="font-semibold">Attempts</span> counts every recorded battle
                      simulation for that exact fight, and{" "}
                      <span className="font-semibold">no verdict by limit</span> counts the battles
                      that reached the simulation safety limit with no win or loss - they are never
                      counted as losses.
                    </span>
                    <span className="mt-1 block">
                      The four metrics use resolved runs only:{" "}
                      <span className="font-semibold">highest potential</span> is the largest
                      opportunity reached by a defeat (the loss gate dispatches none, so it is an
                      opportunity, not a yield),{" "}
                      <span className="font-semibold">highest earned</span> the largest count
                      released by a win, and the two averages are the defeat-only mean opportunity
                      and the mean chests per resolved run of the resident build with the highest
                      such mean. Each average requires at least {averageLeaders?.minSamples ?? 10}
                      qualifying results from that build; n counts qualifying results, not all
                      attempts.
                    </span>
                    <span className="mt-1 block">
                      Clicking any metric opens that exact build's workspace and focuses its fight; a
                      metric the host has not measured reads as unknown and is not clickable. A cell
                      glows green for {Math.round(RECORD_HIGHLIGHT_MS / 1000)}s when its reading has
                      just improved beyond what this library held when it opened.
                    </span>
                  </HelpTip>
                </CardTitle>
                <CardDescription>
                  Five battle lines, four difficulties each. Click a number to open its strategy and
                  focus the fight; the crosshair points new attempts at that encounter.
                </CardDescription>
                {averageLeadersError ? (
                  <p
                    className="mt-2 text-[11px] text-amber-700 dark:text-amber-300"
                    data-encounter-averages-error
                  >
                    The average columns are unavailable ({averageLeadersError}); they stay unknown
                    rather than showing a maximum in their place.
                  </p>
                ) : null}
                {missingEncounters.length ? (
                  <div
                    className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-dashed px-3 py-2"
                    data-optimizer-expand-encounters
                  >
                    <div className="min-w-0 text-[11px] leading-snug text-muted-foreground">
                      <span className="font-medium text-foreground">
                        This library holds {campaignsEncounterCount - missingEncounters.length} of{" "}
                        {campaignsEncounterCount} encounters.
                      </span>
                      <span className="block">
                        Adds supplied baselines for missing encounter/difficulty combinations.
                        Existing runs and strategies are preserved.
                        The library keeps its current simulation horizon.
                      </span>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={!api || busy !== null || status?.state === "Running"}
                      onClick={() => void sendCommand("Expand library", "all_encounters", {})}
                      data-action="expand-encounters"
                      title={
                        status?.state === "Running"
                          ? "Pause the search before expanding the encounter set."
                          : `Add the ${missingEncounters.length} missing encounter(s): ${missingEncounters.join(", ")}`
                      }
                    >
                      <FilePlus2 className="mr-1 h-3.5 w-3.5" />
                      Add missing encounters
                    </Button>
                  </div>
                ) : null}
              </CardHeader>
              <CardContent className="min-w-0">
                {searchFocusIds.length > 0 ? (
                  <div
                    className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/40 bg-primary/5 px-3 py-2"
                    data-optimizer-focus-banner
                  >
                    <div className="min-w-0 text-[11px] leading-snug">
                      <span className="font-semibold text-foreground">
                        Search focus: {searchFocusLabel ?? `encounter ${searchFocus}`}.
                      </span>{" "}
                      <span className="text-muted-foreground" data-optimizer-focus-note>
                        Only selected encounters receive new work. Changes apply after the current batch is saved; clear all toggles to search every encounter.
                      </span>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void setSearchFocus(null)}
                      data-action="clear-encounter-focus"
                    >
                      <X className="mr-1 h-3.5 w-3.5" /> Clear focus
                    </Button>
                  </div>
                ) : null}
                {campaigns.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    The recovered encounter catalogue is not available, so there is nothing to group
                    yet.
                  </p>
                ) : (
                  <div className="grid min-w-0 gap-3 lg:grid-cols-2 min-[1560px]:grid-cols-3">
                    {campaigns.map((campaign) => (
                      <div
                        key={campaign.key}
                        className="min-w-0 overflow-hidden rounded-md border bg-muted/20"
                        data-encounter-card={campaign.key}
                      >
                        <div className="flex items-baseline justify-between gap-2 border-b bg-muted/30 px-3 py-1.5">
                          <span className="truncate text-xs font-semibold">{campaign.title}</span>
                          <span className="shrink-0 text-[9px] uppercase tracking-wide text-muted-foreground">
                            {campaign.difficulties.length} difficulties
                          </span>
                        </div>
                        <div className="w-full min-w-0 overflow-x-auto">
                          {/*
                           * One shared column-header row per card names each reading once. Every
                           * encounter row then carries only the difficulty, its attempts and the four
                           * numbers; the labels are never repeated inside a row. The wrapper scrolls
                           * sideways on its own when a phone is too narrow, so the page never does.
                           */}
                          <div
                            data-encounter-header
                            className="grid grid-cols-[minmax(9rem,1fr)_minmax(3.5rem,auto)_repeat(4,minmax(3.75rem,1fr))] items-center gap-x-1 border-b px-3 py-1 text-[9px] uppercase tracking-wide text-muted-foreground"
                          >
                            <span className="text-left font-medium">Difficulty</span>
                            <span className="text-right font-medium">Attempts</span>
                            <span className="text-right font-medium">Highest potential</span>
                            <span className="text-right font-medium">Highest earned</span>
                            <span className="text-right font-medium">Avg potential</span>
                            <span className="text-right font-medium">Avg earned</span>
                          </div>
                          {campaign.difficulties.map((difficulty) => {
                            const stat = encounterStats[`${difficulty.encounterId}:0`];
                            // The persisted lifetime counter, never a recount of surviving rows.
                            const attempts = stat?.lifetimeAttempts ?? 0;
                            const noVerdict = stat?.lifetimeNoVerdict ?? 0;
                            const fight = `${difficulty.encounterId}:0`;
                            const focused = fightKey === fight;
                            const averages = averageLeadersByFight.get(fight) ?? null;
                            const avgEarned = averages?.earned ?? null;
                            const avgPotential = averages?.potential ?? null;
                            /* The per-cell help id and glow lookup, so both stay keyed by the fight. */
                            const metricHelpId = (metric: string) =>
                              `encounter-metric-help-${difficulty.encounterId}-${metric}`;
                            const isImproved = (metric: string) =>
                              highlightedCells.has(recordCellKey(difficulty.encounterId, 0, metric));
                            const searchFocused = searchFocusIds.includes(difficulty.encounterId);
                            return (
                              <div
                                key={difficulty.encounterId}
                                className={cn(
                                  "grid grid-cols-[minmax(9rem,1fr)_minmax(3.5rem,auto)_repeat(4,minmax(3.75rem,1fr))] cursor-pointer items-center gap-x-1 px-3 py-1.5",
                                  focused && "bg-primary/5",
                                )}
                                data-encounter-difficulty={difficulty.encounterId}
                                data-encounter-focused={focused ? "true" : "false"}
                                data-encounter-search-focus={searchFocused ? "true" : "false"}
                                onClick={() => focusFight(difficulty.encounterId)}
                                title={`Show Strategy families for ${difficulty.title}`}
                              >
                                {/*
                                 * The first cell names the fight and carries its scheduler focus
                                 * toggle; the second is the attempts figure alone, because the header
                                 * row above already names that column. The win/loss/no-verdict split
                                 * stays in the tooltip rather than repeating in every row.
                                 */}
                                <div className="flex min-w-0 items-center gap-1.5">
                                  <div className="min-w-0 flex-1">
                                    <span
                                      className="block truncate text-[11px] font-medium"
                                      title={difficulty.title}
                                      data-encounter-name
                                    >
                                      {difficulty.label}
                                    </span>
                                    <span className="block truncate text-[9px] text-muted-foreground">
                                      Level {formatInt(difficulty.level)} ·{" "}
                                      {formatInt(difficulty.enemyCount)} enemies · enc
                                      {difficulty.encounterId}
                                    </span>
                                  </div>
                                  {/*
                                    The scheduler focus toggle. A real button with a pressed state, so
                                    it is reachable by keyboard and reads its state to assistive
                                    tech; the click never bubbles to the row's own display focus.
                                  */}
                                  <button
                                      type="button"
                                      aria-pressed={searchFocused}
                                      aria-label={
                                        searchFocused
                                          ? `Remove ${difficulty.title} from focused encounters`
                                          : `Add ${difficulty.title} to focused encounters`
                                      }
                                      data-encounter-focus-toggle={difficulty.encounterId}
                                      data-encounter-focus-active={searchFocused ? "true" : "false"}
                                      title={
                                        searchFocused
                                          ? "Selected for search. Click to remove this encounter."
                                          : "Include this encounter in the focused search. You can select several."
                                      }
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        void setSearchFocus(difficulty.encounterId);
                                      }}
                                      className={cn(
                                        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-1 text-[10px] font-medium leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 sm:py-0.5",
                                        searchFocused
                                          ? "border-primary bg-primary/15 text-primary"
                                          : "border-muted-foreground/40 text-muted-foreground hover:bg-primary/10 hover:text-foreground",
                                      )}
                                    >
                                      <Crosshair className="h-3 w-3" />
                                      {searchFocused ? "Focused" : "Focus"}
                                    </button>
                                </div>
                                <div
                                  className="whitespace-nowrap text-right font-mono text-sm font-semibold tabular-nums text-foreground"
                                  title={`${formatInt(stat?.lifetimeWins ?? 0)} wins · ${formatInt(stat?.lifetimeLosses ?? 0)} losses · ${formatInt(noVerdict)} with no verdict by limit`}
                                >
                                  <span data-encounter-attempts>{formatInt(attempts)}</span>
                                </div>
                                <EncounterMetricCell
                                  metric="highest-potential"
                                  label="Highest potential"
                                  helpId={metricHelpId("highest-potential")}
                                  highlighted={isImproved("highest-potential")}
                                  display={
                                    stat?.highestPotentialChests == null
                                      ? "—"
                                      : formatInt(stat.highestPotentialChests)
                                  }
                                  target={`potential:${fight}`}
                                  tooltip={
                                    stat?.highestPotentialChests == null
                                      ? "No resolved defeat has reached a chest opportunity here yet."
                                      : `Highest chest opportunity reached by a resolved defeat over this fight's whole history${stat?.potentialBasis ? ` · ${stat.potentialBasis}` : ""}. Highlights the exact frozen record holder's row in Ranked strategies.`
                                  }
                                  onOpen={() =>
                                    focusEncounterMetric(difficulty.encounterId, "highest-potential")
                                  }
                                />
                                <EncounterMetricCell
                                  metric="highest-earned"
                                  label="Highest earned"
                                  helpId={metricHelpId("highest-earned")}
                                  highlighted={isImproved("highest-earned")}
                                  display={
                                    stat?.highestChestsEarned == null
                                      ? "—"
                                      : formatInt(stat.highestChestsEarned)
                                  }
                                  target={`earned:${fight}`}
                                  action={
                                    stat?.highestChestsEarned == null ? undefined : "focus-record-strategy"
                                  }
                                  tooltip={
                                    stat?.highestChestsEarned == null
                                      ? "No resolved win has released chests here yet, so there is no earned record to point at."
                                      : `Highest chest count released by a resolved win over this fight's whole history${stat?.earnedBasis ? ` · ${stat.earnedBasis}` : ""}. Highlights the exact frozen record holder's row in Ranked strategies.`
                                  }
                                  onOpen={() =>
                                    focusEncounterMetric(difficulty.encounterId, "highest-earned")
                                  }
                                />
                                <EncounterMetricCell
                                  metric="avg-potential"
                                  label="Avg potential"
                                  helpId={metricHelpId("avg-potential")}
                                  highlighted={isImproved("avg-potential")}
                                  display={avgPotential ? formatNumber(avgPotential.mean, 2) : "—"}
                                  note={avgPotential ? `n=${formatInt(avgPotential.samples)}` : null}
                                  target={avgPotential?.candidateId ?? null}
                                  tooltip={
                                    avgPotential
                                      ? averageMetricTooltip("potential", avgPotential, attempts, averageLeaders?.minSamples ?? 10)
                                      : `No resident build has at least ${averageLeaders?.minSamples ?? 10} qualifying defeats with measured potential for this fight yet.`
                                  }
                                  onOpen={() =>
                                    focusEncounterMetric(difficulty.encounterId, "avg-potential")
                                  }
                                />
                                <EncounterMetricCell
                                  metric="avg-earned"
                                  label="Avg earned"
                                  helpId={metricHelpId("avg-earned")}
                                  highlighted={isImproved("avg-earned")}
                                  display={avgEarned ? formatNumber(avgEarned.mean, 2) : "—"}
                                  note={avgEarned ? `n=${formatInt(avgEarned.samples)}` : null}
                                  target={avgEarned?.candidateId ?? null}
                                  tooltip={
                                    avgEarned
                                      ? averageMetricTooltip("earned", avgEarned, attempts, averageLeaders?.minSamples ?? 10)
                                      : `No resident build has at least ${averageLeaders?.minSamples ?? 10} resolved results for this fight yet.`
                                  }
                                  onOpen={() => focusEncounterMetric(difficulty.encounterId, "avg-earned")}
                                />
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>


            {/*
              The ranked list sits directly under the Encounter overview: clicking a difficulty row
              focuses its fight and scrolls here, so this fight's ranked strategies are the next thing
              on screen rather than a distant technical section. The host owns the aggregation and the
              comparability rule; this sorts and renders only what it returns.
            */}
            <Card data-optimizer-encounter-strategies ref={encounterStrategiesRef}>
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Swords className="h-4 w-4" /> Ranked strategies
                    </CardTitle>
                    <p className="mt-1 text-xs font-medium text-foreground/90" data-strategy-list-fight>
                      {fightKey && fightLabel
                        ? `${fightLabel.encounter}${fightLabel.difficulty ? ` · ${fightLabel.difficulty}` : ""}`
                        : "No fight focused — pick a difficulty in the Encounter overview above"}
                    </p>
                    <CardDescription>
                      One row per strategy the host has run for this exact fight, ordered by the observed
                      mean over each build's own sampled runs — an observed mean with the sample count
                      shown, never a claim of certainty, and a single lucky maximum is displayed but does
                      not decide the rank. Range-test probe variants are hidden by default so the
                      principal strategies rank first; the toggle adds them back. Mean potential is a
                      defeats-only opportunity mean, not a yield, and any metric the host has not measured
                      reads as unknown.
                    </CardDescription>
                  </div>
                  <div className="flex flex-col items-start gap-2 sm:items-end" data-strategy-controls>
                    <div className="flex flex-wrap items-center gap-2" data-strategy-sort>
                      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Sort</span>
                      {STRATEGY_SORT_ORDER.map((mode) => (
                        <Button
                          key={mode}
                          type="button"
                          size="sm"
                          variant={strategySort === mode ? "default" : "outline"}
                          onClick={() => setStrategySort(mode)}
                          data-strategy-sort-mode={mode}
                        >
                          {STRATEGY_SORT_LABEL[mode]}
                        </Button>
                      ))}
                    </div>
                    <label
                      className="flex items-center gap-2 text-xs"
                      data-strategy-probe-toggle={showProbeVariants ? "included" : "hidden"}
                      title="Range-test variants the host created while probing a strategy's stat range. Off by default so the principal strategies rank first."
                    >
                      <Checkbox
                        checked={showProbeVariants}
                        onCheckedChange={(checked) => setShowProbeVariants(checked === true)}
                      />
                      Include probe variants
                      {probeVariantCount ? ` (${formatInt(probeVariantCount)})` : ""}
                    </label>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {portfolioFight && portfolioPlan && fightLabel ? (
                  <details className="rounded-lg border p-3"><summary className="cursor-pointer text-sm font-medium">{encounterAwareActive ? "Legacy history - how the previous objective allocated attempts (read-only)" : "How the search is allocating attempts"}</summary><div className="mt-3">
                  <SearchPortfolioPanel
                    plan={portfolioPlan}
                    encounterId={Number(portfolioFight.split(":")[0])}
                    encounterName={`${fightLabel.encounter}${fightLabel.difficulty ? ` · ${fightLabel.difficulty}` : ""}`}
                    rankedById={rankedById}
                    onOpenBuild={openPortfolioBuild}
                  /></div></details>
                ) : null}
                {!fightKey ? (
                  <div className="space-y-3 py-8 text-center" data-strategy-list-empty><p className="text-sm text-muted-foreground">Choose an encounter to compare its strategies.</p><Button onClick={() => setWorkspaceView("overview")}>Choose encounter</Button></div>
                ) : !canEncounterStrategies ? (
                  <p className="text-xs text-muted-foreground" data-strategy-list-unavailable>
                    This host build does not expose encounter_strategies yet, so this fight cannot be
                    ranked. The Strategy families card below still lists what the library holds.
                  </p>
                ) : strategiesBusy ? (
                  <p className="text-xs text-muted-foreground" data-strategy-list-busy>
                    Ranking this fight's strategies…
                  </p>
                ) : strategiesError ? (
                  <Alert variant="destructive" data-strategy-list-error>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>The host did not return this fight's strategies</AlertTitle>
                    <AlertDescription>
                      <p className="font-mono text-[11px]">{strategiesError}</p>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="mt-2"
                        onClick={() => void loadEncounterStrategies(fightKey)}
                        data-action="reload-encounter-strategies"
                      >
                        <RefreshCw className="mr-1 h-3.5 w-3.5" /> Try again
                      </Button>
                    </AlertDescription>
                  </Alert>
                ) : (
                  <>
                    {/*
                      The overview metric follow-through. The click focused this fight and named the
                      exact reading that must be highlighted; the note says which row owns it, or -
                      when the ranked list holds no such row - explains why nothing is highlighted and
                      offers the reading's own investigation directly. A different build is never
                      substituted for the one the reading names.
                    */}
                    {overviewMetricFocus ? (
                      <p className="text-[11px] leading-snug text-muted-foreground" data-strategy-focus-note>
                        <span className="font-medium text-foreground">Overview reading: </span>
                        <span data-strategy-focus-provenance>{overviewMetricFocus.provenance}</span>
                        {overviewMetricOwner.pending
                          ? " — waiting for this fight's ranked strategies to load…"
                          : overviewMetricOwner.missing
                            ? ""
                            : " — the marked row below is its exact owner. Open its build name to investigate it."}
                      </p>
                    ) : null}
                    {!overviewMetricOwner.pending && overviewMetricOwner.missing && overviewMetricFocus ? (
                      <Alert variant="destructive" data-strategy-focus-missing>
                        <AlertTriangle className="h-4 w-4" />
                        <AlertTitle>No ranked row owns this reading</AlertTitle>
                        <AlertDescription>
                          <p>{overviewMetricOwner.missing}</p>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="mt-2"
                            onClick={() => {
                              if (overviewMetricFocus.holderKey) {
                                void openInvestigation({ holderKey: overviewMetricFocus.holderKey });
                              } else if (overviewMetricFocus.candidateId) {
                                void openInvestigation({ candidateId: overviewMetricFocus.candidateId });
                              }
                            }}
                            data-action="investigate-metric-owner"
                          >
                            Open its investigation directly
                          </Button>
                        </AlertDescription>
                      </Alert>
                    ) : null}
                    {visibleStrategies.length === 0 && probeVariantCount > 0 ? (
                      <div
                        className="space-y-2 rounded-md border border-dashed p-3"
                        data-strategy-list-probe-only
                      >
                        <p className="text-xs text-muted-foreground">
                          Every ranked strategy for this fight is a range-test probe variant —{" "}
                          {probeVariantLabel} hidden here, and no principal strategy to rank yet.
                        </p>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => setShowProbeVariants(true)}
                          data-action="show-probe-variants"
                        >
                          Show {probeVariantLabel}
                        </Button>
                      </div>
                    ) : null}
                    {visibleStrategies.length === 0 && probeVariantCount === 0 && orphanHolders.length === 0 ? (
                      <p className="text-xs text-muted-foreground" data-strategy-list-empty>
                        The host has no scored strategy for this fight yet.
                      </p>
                    ) : null}
                    {!showProbeVariants && probeVariantCount > 0 && visibleStrategies.length > 0 ? (
                      <p className="text-[11px] text-muted-foreground" data-strategy-probe-hidden>
                        {probeVariantLabel} hidden. These are rungs the host recorded while measuring a
                        strategy's stat range, not separate principal strategies. Turn on Include probe
                        variants above to rank them here, or open a build's investigation panel to inspect
                        the ranges measured for it.
                      </p>
                    ) : null}
                    {comparisonIds.length ? <div className="space-y-2 rounded-xl border p-4" data-strategy-comparison>
                      <div className="flex items-center justify-between"><h3 className="font-semibold">Compare selected builds</h3><Button size="sm" variant="ghost" onClick={() => setComparisonIds([])}>Clear</Button></div>
                      <div className="grid gap-3 md:grid-cols-3">{visibleStrategies.filter(row => comparisonIds.includes(row.candidateId)).map(row =>
                        <button key={row.candidateId} className="space-y-2 rounded-lg border p-3 text-left hover:bg-muted/40" onClick={() => selectEncounterStrategy(row)}>
                          <div className="text-sm font-semibold" title={strategyRowTitle(row)}>{strategyRowName(row)}</div><div className="text-2xl font-semibold">{formatNumber(row.meanEarned, 2)} <span className="text-xs font-normal">mean earned</span></div>
                          <p className="text-xs text-muted-foreground">{formatInt(row.earnedSamples)} earned samples · {formatPercent(row.winRate)} wins</p>
                          <p className="text-xs">Best earned {formatNumber(row.bestEarned, 0)} · {formatInt(row.noVerdict)} unresolved</p>
                          {row.eaEarnedSamples ? <p className="text-xs text-muted-foreground">Community window: mean {formatNumber(row.eaMeanEarned, 2)} · best {formatNumber(row.eaBestEarned, 0)} · n={formatInt(row.eaEarnedSamples)}</p> : null}
                          <p className="text-xs text-muted-foreground">{row.comparable ? "Comparable evidence" : "Provisional evidence"}</p>
                        </button>)}</div><p className="text-xs text-muted-foreground">Select up to three builds. Different sample sizes affect how much confidence to place in their averages.</p>
                    </div> : null}
                    {visibleStrategies.length ? (
                      <div className="overflow-x-auto">
                        <Table data-strategy-list>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Compare</TableHead>
                              <TableHead>#</TableHead>
                              <TableHead>Build</TableHead>
                              <TableHead className="text-right">Attempts</TableHead>
                              <TableHead>W / L / no verdict</TableHead>
                              <TableHead
                                className="text-right"
                                title="Mean chests earned over the build's resolved runs; n is the earned sample count"
                              >
                                Mean earned
                              </TableHead>
                              <TableHead
                                className="text-right"
                                title="Mean chest opportunity over defeats only; n is the loss sample count. An opportunity, never a yield."
                              >
                                Mean potential · loss-only
                              </TableHead>
                              <TableHead className="text-right" title="Compatible Community window mean / maximum from the same resolved finalEarned outcomes">Community window · mean / best</TableHead>
                              <TableHead className="text-right">Best earned</TableHead>
                              <TableHead className="text-right">Best potential</TableHead>
                              <TableHead />
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {visibleStrategies.map((row, index) => {
                              const reliability = strategyRowReliability(row);
                              /*
                               * The row an overview reading owns. Only the exact candidate the
                               * reading named is ever marked; no neighbour is substituted.
                               */
                              const highlighted =
                                !overviewMetricOwner.pending &&
                                overviewMetricOwner.row?.candidateId === row.candidateId;
                              return (
                                <TableRow
                                  key={row.candidateId}
                                  data-strategy-row={row.candidateId}
                                  data-strategy-rank={index + 1}
                                  data-strategy-comparable={row.comparable ? "true" : "false"}
                                  data-strategy-row-focus={highlighted ? "true" : "false"}
                                  aria-current={highlighted ? "true" : undefined}
                                  onClick={() => selectEncounterStrategy(row)}
                                  className={cn(
                                    "cursor-pointer",
                                    highlighted && "ka-strategy-row-focus",
                                  )}
                                >
                                  <TableCell onClick={event => event.stopPropagation()}><input type="checkbox" aria-label={`Compare ${strategyRowName(row)}`} checked={comparisonIds.includes(row.candidateId)} disabled={!comparisonIds.includes(row.candidateId) && comparisonIds.length >= 3} onChange={event => setComparisonIds(ids => event.target.checked ? [...ids, row.candidateId] : ids.filter(id => id !== row.candidateId))} className="h-4 w-4 accent-primary" /></TableCell>
                                  <TableCell className="text-xs tabular-nums">{index + 1}</TableCell>
                                  <TableCell>
                                    <button
                                      type="button"
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        selectEncounterStrategy(row);
                                      }}
                                      data-action="select-encounter-strategy"
                                      title={strategyRowTitle(row)}
                                      aria-label={`Open the investigation panel for ${strategyRowName(row)}`}
                                      className="block w-full min-w-0 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                                    >
                                      <span
                                        className="block text-xs font-medium text-foreground underline-offset-2 hover:underline"
                                        data-strategy-name={strategyRowName(row)}
                                        data-strategy-name-derived={row.nameDerived ? "true" : "false"}
                                      >
                                        {strategyRowName(row)}
                                      </span>
                                      <span className="block font-mono text-[10px] text-muted-foreground">
                                        {row.source} · {shortId(row.candidateId)}
                                      </span>
                                    </button>
                                    {highlighted ? (
                                      <span className="mt-1 flex flex-wrap items-center gap-1">
                                        <span
                                          className="inline-flex items-center rounded-full border border-primary/60 bg-primary/15 px-1.5 py-0.5 text-[9px] font-medium leading-none text-primary"
                                          data-strategy-row-owner
                                        >
                                          Owner of this reading
                                        </span>
                                        <span
                                          className="inline-flex items-center rounded-full border border-primary/50 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium leading-none text-primary"
                                          data-strategy-row-provenance
                                        >
                                          {overviewMetricFocus?.provenance}
                                        </span>
                                      </span>
                                    ) : null}
                                  </TableCell>
                                  <TableCell className="text-right text-xs tabular-nums">
                                    {formatInt(row.attempts)}
                                  </TableCell>
                                  <TableCell className="text-xs tabular-nums">
                                    {formatInt(row.wins)} / {formatInt(row.losses)} / {formatInt(row.noVerdict)}
                                  </TableCell>
                                  <TableCell className="text-right text-xs tabular-nums" data-strategy-mean-earned>
                                    {row.meanEarned == null && (row.windows?.length ?? 0) > 1 ? "Separate readings" : formatMetric(row.meanEarned)}{" "}
                                    <span className="text-[10px] text-muted-foreground">
                                      n={formatInt(row.earnedSamples)}
                                    </span>
                                  </TableCell>
                                  <TableCell className="text-right text-xs tabular-nums" data-strategy-mean-potential>
                                    {row.meanPotential == null && (row.windows?.length ?? 0) > 1 ? "Separate readings" : formatMetric(row.meanPotential)}{" "}
                                    <span className="text-[10px] text-muted-foreground">
                                      n={formatInt(row.potentialSamples)}
                                    </span>
                                  </TableCell>
                                  <TableCell className="text-right text-xs tabular-nums" data-strategy-ea-earned>
                                    {row.eaEarnedSamples
                                      ? `${formatMetric(row.eaMeanEarned)} / ${formatMetric(row.eaBestEarned, 0)}`
                                      : "—"}{" "}
                                    {row.eaEarnedSamples ? <span className="text-[10px] text-muted-foreground">n={formatInt(row.eaEarnedSamples)}</span> : null}
                                  </TableCell>
                                  <TableCell className="text-right text-xs tabular-nums">
                                    {formatMetric(row.bestEarned, 0)}
                                  </TableCell>
                                  <TableCell className="text-right text-xs tabular-nums">
                                    {formatMetric(row.bestPotential, 0)}
                                  </TableCell>
                                  <TableCell>
                                    <span title={reliability.detail}>
                                      <ToneBadge category={reliability.category}>{reliability.label}</ToneBadge>
                                    </span>
                                  </TableCell>
                                </TableRow>
                              );
                            })}
                          </TableBody>
                        </Table>
                      </div>
                    ) : null}
                    {orphanHolders.length ? (
                      <div className="space-y-2 rounded-md border border-dashed p-3" data-strategy-orphans>
                        <p className="text-[11px] font-medium text-foreground/90">
                          Pruned record holders — the candidate is gone, the frozen scenario is not
                        </p>
                        <p className="text-[11px] text-muted-foreground">
                          These encounter records still replay and investigate from their frozen scenario
                          and seed pair, even though the build that set them was pruned.
                        </p>
                        {orphanHolders.map((holder) => {
                          /*
                           * A pruned holder can be the exact owner of an overview reading. When it is,
                           * its own row is the one marked - never a different resident build - and its
                           * name opens the frozen investigation just like a ranked build name does.
                           */
                          const highlighted =
                            !overviewMetricOwner.pending && overviewMetricOwner.holder?.key === holder.key;
                          return (
                            <div
                              key={holder.key}
                              className={cn(
                                "flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/20 px-3 py-2",
                                highlighted && "ka-strategy-orphan-focus",
                              )}
                              data-strategy-orphan={holder.key}
                              data-strategy-orphan-focus={highlighted ? "true" : "false"}
                              aria-current={highlighted ? "true" : undefined}
                            >
                              <div className="min-w-0">
                                <button
                                  type="button"
                                  onClick={() => openOrphanHolder(holder)}
                                  data-action="investigate-orphan-holder"
                                  title={`Open the frozen investigation panel for ${holder.label ?? holder.key}`}
                                  aria-label={`Open the frozen investigation panel for ${holder.label ?? holder.key}`}
                                  className="block rounded-sm text-left text-xs font-medium text-foreground underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 hover:underline"
                                >
                                  {holder.label ?? holder.key}
                                </button>
                                <div className="font-mono text-[10px] text-muted-foreground">
                                  {holder.key} · frozen value {formatInt(holder.value)}
                                  {holder.candidateId ? ` · pruned ${shortId(holder.candidateId)}` : ""}
                                </div>
                                {highlighted ? (
                                  <span
                                    className="mt-1 inline-flex items-center rounded-full border border-primary/60 bg-primary/15 px-1.5 py-0.5 text-[9px] font-medium leading-none text-primary"
                                    data-strategy-orphan-provenance
                                  >
                                    {overviewMetricFocus?.provenance}
                                  </span>
                                ) : null}
                              </div>
                              <div className="flex flex-wrap items-center gap-2">
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="outline"
                                  disabled={!api || replayBlocked}
                                  onClick={() =>
                                    void runHolderReplay(
                                      holder.key,
                                      `${holder.label ?? holder.key} · frozen record`,
                                    )
                                  }
                                  data-action="replay-orphan-holder"
                                  title="Replay the frozen run that set this record"
                                >
                                  <Swords className="mr-1 h-3.5 w-3.5" /> Replay frozen run
                                </Button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </>
                )}
              </CardContent>
            </Card>

            {/*
              The selected strategy's workspace: one exact build, reachable from the ranked list or
              from the earned-record number, and it opens at its own heading. Reading order is identity
              and combat team, the simulate/replay actions, the result of the last batch run from here,
              then the deeper Build, measured-ranges and run-history sections. A null metric stays
              unknown and a loss-only mean is an opportunity, never a yield.
            */}
            {investigationTarget ? (
              <Card
                data-optimizer-investigation
                data-detail-tab={detailTab}
                ref={investigationRef}
                className="min-h-[calc(100vh-150px)] scroll-mt-[150px]"
              >
                <CardHeader className="pb-3">
                  <div className="flex flex-wrap items-start justify-between gap-3" data-workspace-top>
                    <div className="min-w-0">
                      <CardTitle
                        className="flex flex-wrap items-center gap-x-2 gap-y-1 text-base"
                        data-workspace-heading
                      >
                        <Layers className="h-4 w-4" />
                                                <span
                          className="text-base font-semibold"
                          data-investigation-heading-build
                        >
                          {investigationLabel}
                        </span>
                      </CardTitle>
                      <CardDescription>One exact build · {encounterLabel(investigationEncounterId, investigationEncounterId == null ? undefined : encounters?.[String(investigationEncounterId)])}</CardDescription>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={closeInvestigation}
                      data-action="close-investigation"
                    >
                      <ChevronLeft className="mr-1 h-3.5 w-3.5" /> Close
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap" data-strategy-quick-actions>
                    <Button size="sm" variant="outline" disabled={!canReplayInvestigation || replayBlocked || !storedRuns.some(run => runSeeds(run))} onClick={() => {
                      const best = [...storedRuns].filter(run => runSeeds(run)).sort((a, b) => (runEarnedValue(b) ?? -1) - (runEarnedValue(a) ?? -1))[0];
                      if (best) void replayInvestigationRun(best);
                    }}>Watch best saved run</Button>
                    <Button size="sm" variant="outline" disabled={!canSimulateVisual || simulationRunning} onClick={() => void simulateStrategyVisual()}>Try new RNG & watch</Button>
                    <Button size="sm" onClick={() => setDetailTab("ranges")}>Refine strategy</Button>
                    <Button size="sm" variant="outline" onClick={() => setDetailTab("test")}>Run a batch</Button>
                  </div>
                  <nav className="flex gap-1 overflow-x-auto border-b pb-2" aria-label="Strategy sections" data-strategy-tabs>
                    {([["summary", "Results"], ["team", "Team & skills"], ["ranges", "Ranges & refinement"], ["runs", "Saved runs"], ["test", "New trials"]] as const).map(([tab, label]) =>
                      <Button key={tab} size="sm" variant={detailTab === tab ? "secondary" : "ghost"} aria-current={detailTab === tab ? "page" : undefined} onClick={() => setDetailTab(tab)} data-detail-link={tab}>{label}</Button>)}
                  </nav>
                  {investigationBusy ? (
                    <p className="text-xs text-muted-foreground" data-investigation-busy>
                      Loading this strategy's stored runs and frozen scenario…
                    </p>
                  ) : null}
                  {investigationError ? (
                    <Alert variant="destructive" data-investigation-error>
                      <AlertTriangle className="h-4 w-4" />
                      <AlertTitle>The host did not return this strategy</AlertTitle>
                      <AlertDescription>
                        <p className="font-mono text-[11px]">{investigationError}</p>
                      </AlertDescription>
                    </Alert>
                  ) : null}

                  {investigationDetail ? (
                    <>
                      {/*
                        The immediate answer to "what has been tested for this build, and which
                        worked?": the parent-anchored fine-tuning programs in brief, placed before the
                        long per-slot build detail so a reader arriving from an overview metric sees
                        the tested values and the evidence boundary before any recorded raw stat.
                      */}
                      <div className="space-y-4" data-strategy-summary>
                        <OutcomeHistogram distribution={investigationDetail.outcomeDistribution} />
                        <RangeSummary programs={fineTunePrograms} compact />
                      </div>
                      <div data-strategy-team-overview><StrategyFormationPanel slots={investigationTeamBuild} formation={investigationDetail.formation} /></div>
                      <div className="space-y-3" data-strategy-range-summary>
                        {investigationDetail.resident === false && investigationTarget.holderKey && api?.restore_strategy ? <div className="rounded-lg border p-3">
                          <p className="mb-2 text-sm">This record's build was removed from the active search. Restore its frozen setup to run new refinement experiments.</p>
                          <Button size="sm" disabled={busy !== null || simulationRunning || replayBusy} onClick={() => void restoreFrozenStrategy()}>{busy === "Restoring frozen strategy" ? "Saving & restoring…" : "Restore build for refinement"}</Button>
                        </div> : null}
                        <RangeSummary programs={fineTunePrograms} />
                      </div>
                      <TestedStatValues
                        parentId={fineTuneParentId}
                        parentLabel={fineTuneParentLabel}
                        parentResident={fineTuneParentResident}
                        selectedId={investigationCandidateId}
                        selectedLabel={investigationLabel}
                        onOpenParentRanges={openFineTuneChild}
                        programs={fineTunePrograms}
                        programsLoading={
                          fineTunedPrograms === null &&
                          canReadFineTunePrograms &&
                          fineTunePrograms.length === 0
                        }
                        programsError={fineTuneError}
                        canReadPrograms={canReadFineTunePrograms}
                        autoTune={status?.autoTune ?? null}
                        onOpenAutoTuneParent={openAutoTuneParent}
                      />

                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" data-investigation-identity>
                        <StatTile
                          label="Build"
                          value={investigationLabel}
                          hint={
                            investigationCandidate
                              ? `candidate ${shortId(investigationCandidate.id)}`
                              : investigationCandidateId
                                ? `pruned candidate ${shortId(investigationCandidateId)} · frozen record`
                                : "frozen record"
                          }
                        />
                        <StatTile
                          label="Source"
                          value={investigationCandidate?.source ?? "unknown"}
                          hint={
                            investigationCandidate
                              ? `family ${investigationCandidate.family ?? NO_FAMILY}`
                              : "not held in the live library"
                          }
                        />
                        <StatTile
                          label="Encounter"
                          value={encounterLabel(
                            investigationEncounterId,
                            investigationEncounterId != null
                              ? encounters?.[String(investigationEncounterId)]
                              : undefined,
                          )}
                          hint={
                            investigationEncounterId != null
                              ? `enc${investigationEncounterId}`
                              : "scenario carries no encounter id"
                          }
                        />
                        <StatTile
                          label="Allies"
                          value={
                            investigationParty.allies.length
                              ? investigationParty.allies.join(", ")
                              : "none recorded"
                          }
                          hint={`pets: ${
                            investigationParty.pets.length ? investigationParty.pets.join(", ") : "none"
                          }`}
                        />
                      </div>

                      {/*
                        The reading order this workspace is built for: what the build is, the actions
                        that test it, and the result of the batch just run. Everything deeper - the
                        exact build, the measured ranges, the full run ledger - follows below.
                      */}
                      <div className="space-y-1.5" data-investigation-combat>
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Combat team
                        </p>
                        <TeamCombatOverview slots={investigationTeamBuild} />
                      </div>

                      {/*
                        The primary actions. Both simulations use the exact frozen build on brand-new
                        seed pairs; neither replays the stored record. The count box accepts any total
                        in range and is split into host calls of at most eight.
                      */}
                      <div className="space-y-2 rounded-md border bg-muted/30 p-3" data-investigation-actions>
                        <p className="text-sm font-semibold">Test this exact build on fresh RNG</p>
                        <p className="text-xs text-muted-foreground">A saved worker batch measures this fixed build and contributes to totals and shared evidence. Ranges & refinement sweeps stats broadly, then narrows measured regions. Skill tests compare removals on shared seeds, including removing all copies across the team.</p>
                        {trialRunning ? <Button size="sm" variant="outline" onClick={() => { trialCancel.current = true; }}>Stop after current chunk</Button> : null}
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="flex items-center gap-1.5 text-xs font-medium">
                            Simulate
                            <Input
                              className="h-8 w-16 text-center text-xs tabular-nums"
                              inputMode="numeric"
                              value={trialCountInput}
                              onChange={(event) => setTrialCountInput(event.target.value)}
                              onBlur={() =>
                                setTrialCountInput((current) =>
                                  String(clampTrialCount(parseTrialCount(current) ?? FRESH_TRIAL_DEFAULT)),
                                )
                              }
                              onKeyDown={(event) => {
                                if (event.key !== "Enter") return;
                                event.preventDefault();
                                if (!canRunStrategy || simulationRunning) return;
                                setTrialCountInput(String(trialCount));
                                void runFreshTrials(trialCount);
                              }}
                              disabled={!canRunStrategy || simulationRunning}
                              data-trial-count-input
                              aria-label={`Number of fresh fights to simulate (${FRESH_TRIAL_MIN} to ${FRESH_TRIAL_MAX})`}
                              title={`${FRESH_TRIAL_MIN} to ${FRESH_TRIAL_MAX} fights`}
                            />
                            fights
                          </label>
                          <Button
                            type="button"
                            size="sm"
                            disabled={!canRunStrategy || simulationRunning}
                            onClick={() => {
                              setTrialCountInput(String(trialCount));
                              void runFreshTrials(trialCount);
                            }}
                            data-action="run-fresh-trials-count"
                            title={`Run ${formatInt(trialCount)} new fights as a persistent worker job`}
                          >
                            <Play className="mr-1 h-3.5 w-3.5" /> Simulate {formatInt(trialCount)} fight
                            {trialCount === 1 ? "" : "s"}
                          </Button>
                          {api?.start_strategy_experiment ? <Button size="sm" variant="outline" disabled={simulationRunning || Boolean(busy) || trialCount < 128 || encounterAwareActive} onClick={() => void startExperiment(trialCount, "skills")}>Test skill importance · {formatInt(trialCount)} per build</Button> : null}
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={!canSimulateVisual || simulationRunning || replayBusy}
                            onClick={() => void simulateStrategyVisual()}
                            data-action="simulate-visually"
                            title="Run one brand-new fight of this exact build and open its live trace"
                          >
                            <Eye className="mr-1 h-3.5 w-3.5" /> Simulate 1 visually
                          </Button>
                          {investigationTarget.holderKey ? (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              disabled={replayBlocked || !replayableHolder(investigationTarget.holderKey)}
                              onClick={() =>
                                void runHolderReplay(investigationTarget.holderKey!, investigationLabel)
                              }
                              data-action="replay-record"
                              title="Reproduce the stored record on its own seed pair; this is not a fresh simulation"
                            >
                              <Swords className="mr-1 h-3.5 w-3.5" /> Replay record
                            </Button>
                          ) : null}
                        </div>
                        <div className="flex flex-wrap items-center gap-2" data-trial-controls>
                          {FRESH_TRIAL_TOTALS.map((total) => (
                            <Button
                              key={total}
                              type="button"
                              size="sm"
                              variant="outline"
                              disabled={!canRunStrategy || simulationRunning}
                              onClick={() => void runFreshTrials(total)}
                              data-action={`run-fresh-trials-${total}`}
                            >
                              {total} fights
                            </Button>
                          ))}
                          <span className="text-[10px] text-muted-foreground" data-trial-count-hint>
                            saved job · pauses at the requested count · Start resumes after a pause
                          </span>
                        </div>
                        {trialCountInvalid ? (
                          <p className="text-[11px] text-amber-600 dark:text-amber-500" data-trial-count-clamped>
                            Entered {formatInt(trialCountEntry)} — clamped to {formatInt(trialCount)} (
                            {FRESH_TRIAL_MIN}–{FRESH_TRIAL_MAX}).
                          </p>
                        ) : null}
                        {trialRunning ? (
                          <div className="space-y-1">
                            <Progress
                              value={trialTotal ? (trialDone / trialTotal) * 100 : 0}
                              data-trial-progress-bar
                            />
                            <p className="text-[11px] text-muted-foreground" data-trial-progress>
                              Running fresh trials — {formatInt(trialDone)} / {formatInt(trialTotal)} complete
                            </p>
                          </div>
                        ) : null}
                        {trialError ? (
                          <p className="text-xs text-destructive" data-trial-error>
                            {trialError}
                          </p>
                        ) : null}
                        {visualRunning ? (
                          <p className="text-[11px] text-muted-foreground" data-visual-progress>
                            Running one fresh fight of this exact build and drawing its trace…
                          </p>
                        ) : null}
                        {visualError ? (
                          <p className="text-xs text-destructive" data-visual-error>
                            {visualError}
                          </p>
                        ) : null}
                        {visualNote ? (
                          <p className="text-[11px] text-muted-foreground" data-visual-note>
                            {visualNote}
                          </p>
                        ) : null}
                        {!canRunStrategy ? (
                          <p className="text-[11px] text-muted-foreground" data-trial-unavailable>
                            This host build does not expose run_strategy yet, so fresh trials are
                            unavailable.
                          </p>
                        ) : null}
                        {!canSimulateVisual ? (
                          <p className="text-[11px] text-muted-foreground" data-visual-unavailable>
                            This host build does not expose simulate_strategy_visual yet, so a one-fight
                            visual simulation is unavailable.
                          </p>
                        ) : null}
                      </div>

                      {api?.start_strategy_experiment ? (
                        <div className="space-y-3 rounded-md border p-3" data-saved-batch-results>
                          <p className="text-sm font-semibold">Saved batch rewards</p>
                          <p className="text-xs text-muted-foreground">
                            Fresh fights of this exact build. Comparing batches shows variation in its measured
                            rewards; these tests do not change the strategy. Defeats count as zero earned chests.
                          </p>
                          {scopedBatches?.error ? <p className="text-xs text-destructive" role="alert">{scopedBatches.error}</p> : null}
                          {!scopedBatches ? <p className="text-xs text-muted-foreground">Loading saved batches…</p>
                            : !scopedBatches.supported ? <p className="text-xs text-muted-foreground">Reopen the updated optimiser to load saved worker batch results.</p>
                            : scopedBatches.batches.length === 0 ? <p className="text-xs text-muted-foreground">No saved worker batches found for this build. Completed batches will appear here automatically.</p>
                            : <>
                              <div className="overflow-x-auto">
                                <Table>
                                  <TableHeader><TableRow>
                                    <TableHead>Batch · newest first</TableHead><TableHead>Fights saved</TableHead>
                                    <TableHead>Mean earned</TableHead><TableHead>Change from previous batch</TableHead>
                                    <TableHead>Wins / losses / unresolved</TableHead>
                                  </TableRow></TableHeader>
                                  <TableBody>{scopedBatches.batches.map((batch, index, batches) => {
                                    const reading = batch.comparisons?.find(row => row.candidateId === batch.candidateId);
                                    const previous = batches[index + 1];
                                    const before = previous?.comparisons?.find(row => row.candidateId === previous.candidateId);
                                    const delta = reading?.meanEarned != null && before?.meanEarned != null
                                      ? reading.meanEarned - before.meanEarned : null;
                                    return <TableRow key={batch.id} data-saved-batch={batch.id}>
                                      <TableCell>{batch.createdAt ? new Date(batch.createdAt * 1000).toLocaleString() : shortId(batch.id)}
                                        <span className="block text-xs text-muted-foreground">{batch.status}{batch.status !== "complete" ? " · partial results" : ""}</span></TableCell>
                                      <TableCell>{formatInt(reading?.runs ?? batch.completed)} / {formatInt(batch.requestedPerBuild)}</TableCell>
                                      <TableCell>{reading?.meanEarned == null ? "—" : `${formatNumber(reading.meanEarned, 2)} chests`}
                                        <span className="block text-xs text-muted-foreground">{formatInt(reading?.earnedSamples)} reward samples</span></TableCell>
                                      <TableCell>{delta == null ? "—" : `${delta > 0 ? "+" : ""}${formatNumber(delta, 2)} chests/fight`}</TableCell>
                                      <TableCell>{formatInt(reading?.wins)} / {formatInt(reading?.losses)} / {formatInt(reading?.unresolved)}</TableCell>
                                    </TableRow>;
                                  })}</TableBody>
                                </Table>
                              </div>
                              <p className="text-xs text-muted-foreground">Latest 20 batches for this build. Changes are observed averages on different random seeds, not proof of improvement. Unresolved rewards are excluded from the mean.</p>
                            </>}
                        </div>
                      ) : latestBatch ? (
                        <LatestTestCard
                          batch={latestBatch}
                          runMoreCount={trialCount}
                          busy={simulationRunning}
                          canSimulateVisual={canSimulateVisual && !replayBusy}
                          canInspect={Boolean(latestBatchBest) && canReplayInvestigation}
                          onRunMore={() => void runFreshTrials(trialCount)}
                          onSimulateVisual={() => void simulateStrategyVisual()}
                          onInspect={() => {
                            if (latestBatchBest) void replayInvestigationRun(latestBatchBest);
                          }}
                        />
                      ) : (
                        <p className="text-xs text-muted-foreground" data-latest-test-empty>
                          No batch has been run from here yet. Simulate a few fights above and this
                          card fills in with what they showed.
                        </p>
                      )}

                      <div className="space-y-2" data-investigation-notable-section>
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Runs worth watching
                        </p>
                        <NotableRuns
                          runs={[...storedRuns, ...trialRuns]}
                          canReplay={canReplayInvestigation}
                          busy={simulationRunning}
                          onReplay={(run) => void replayInvestigationRun(run)}
                        />
                      </div>
                      <TeamBuildSection slots={investigationTeamBuild} />

                      {/*
                        Fine tuning, grouped under the parent build: the host's own program views for
                        this frozen parent (a tested point links out to its own child build), the
                        automatic-tuning diagnostic when nothing was started, and the one manual
                        "sweep another statistic" control. It is a controlled experiment beside the
                        ranked list, never a dump of the tested points into it.
                      */}
                      <FineTuneSection
                        parentId={fineTuneParentId}
                        parentLabel={fineTuneParentLabel}
                        resident={fineTuneParentResident}
                        programs={fineTunePrograms}
                        programsLoading={
                          fineTunedPrograms === null &&
                          canReadFineTunePrograms &&
                          fineTunePrograms.length === 0
                        }
                        programsError={fineTuneError}
                        canReadPrograms={canReadFineTunePrograms}
                        autoTune={status?.autoTune ?? null}
                        autoTunePublished={status?.autoTune !== undefined}
                        unitOptions={investigationParty.allies}
                        canStart={Boolean(api) && !simulationRunning && !encounterAwareActive}
                        starting={fineTuneStarting}
                        startError={fineTuneStartError}
                        onStart={(request) => void startFineTune(request)}
                        onOpenChild={openFineTuneChild}
                        onOpenAutoTuneParent={openAutoTuneParent}
                        onFocusEncounter={(encounterId) => void setSearchFocus(encounterId)}
                        backParent={
                          fineTuneChildOrigin?.candidateId
                            ? {
                                candidateId: fineTuneChildOrigin.candidateId,
                                label: fineTuneChildOrigin.label ?? "the frozen parent",
                              }
                            : null
                        }
                        onBackToParent={() => {
                          if (!fineTuneChildOrigin?.candidateId) return;
                          setSelectedId(fineTuneChildOrigin.candidateId);
                          setFamilyKey(null);
                          setExampleLabel(null);
                          void openInvestigation({ candidateId: fineTuneChildOrigin.candidateId });
                        }}
                      />

                      <details className="rounded-lg border p-3" data-investigation-ranges>
                        <summary className="cursor-pointer select-none text-sm font-semibold">
                          Measured ranges &amp; probes — the host's own measured bounds
                        </summary>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          Advanced. The host picks the ladder and its rungs; an unbracketed bound stays
                          unbracketed, and an unestablished axis needs the explicit opt-in.
                        </p>
                        <div className="mt-3" data-investigation-probes>
                        {investigationCandidate ? (
                          <>
                            {investigationProbes.length ? (
                              <div className="space-y-3">
                                {investigationProbes.map((probe, index) => (
                                  <ProbeAnalysis
                                    key={`${probe.pivotId}-${probe.axis}-${probe.axis2 ?? ""}-${index}`}
                                    probe={probe}
                                    lastProbe={lastProbe}
                                  />
                                ))}
                              </div>
                            ) : (
                              <p className="text-xs text-muted-foreground" data-investigation-probes-empty>
                                The host has measured no threshold or grid for this build yet, so every
                                untested value stays unclaimed.
                              </p>
                            )}
                            <div className="pt-3">
                              {encounterAwareActive ? (
                                <p className="text-[11px] text-muted-foreground" data-probe-encounter-block>
                                  Range probes are replaced by the encounter-aware boundary questions
                                  above while that mode is active; the measured probes below stay
                                  readable as history.
                                </p>
                              ) : (
                                <>
                                  <ProbeAxisControl
                                    disabled={!api || busy !== null}
                                    starting={busy?.startsWith("Probe ") ?? false}
                                    axis={probeAxis}
                                    axis2={probeAxis2}
                                    allowUnestablished={allowUnestablished}
                                    onAxis={setProbeAxis}
                                    onAxis2={setProbeAxis2}
                                    onAllowUnestablished={handleAllowUnestablished}
                                    onStart={() => void startProbeFor(investigationCandidate.id)}
                                  />
                                  <p className="mt-1 text-[10px] text-muted-foreground">
                                    The host picks the ladder and its rungs; an illegal ladder or an
                                    unestablished axis is refused with the host's own reason.
                                  </p>
                                </>
                              )}
                            </div>
                          </>
                        ) : (
                          <p className="text-xs text-muted-foreground" data-investigation-probes-needs-restore>
                            This target has no live build resident in the library, so no range probe can
                            be run or read. Range probing needs the build restored or re-imported to the
                            library first; until then no probes are shown, and no other candidate's
                            ranges are shown in its place.
                          </p>
                        )}
                      </div>
                      </details>

                      <details open className="rounded-lg border p-3" data-investigation-history>
                        <summary className="cursor-pointer select-none text-sm font-semibold">
                          Run history &amp; technical details — stored bank, every run, seed and digest
                        </summary>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          The full ledger behind the summary above, closed by default. Nothing is
                          hidden: every retained stored run and every fresh trial keeps its seeds,
                          its digest and its own Replay button.
                        </p>
                        <div className="mt-3 space-y-4">

                      <div className="grid gap-3 sm:grid-cols-2" data-investigation-record>
                        <div className="rounded-md border p-3 text-xs">
                          <p className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">
                            Frozen record
                          </p>
                          {investigationHolder ? (
                            <ul className="space-y-1">
                              <li>
                                value{" "}
                                <span className="font-mono" data-investigation-holder-value>
                                  {formatInt(investigationHolder.value)}
                                </span>
                              </li>
                              <li>
                                verdict{" "}
                                <span className="font-mono">{verdictLabel(investigationHolder.verdict)}</span>
                              </li>
                              <li>
                                seed pair{" "}
                                <span className="font-mono" data-investigation-seeds>
                                  {investigationSeedPair ?? "unknown"}
                                </span>{" "}
                                (same-seed reproduction)
                              </li>
                              <li>
                                score basis{" "}
                                <span className="font-mono">{investigationHolder.basis ?? "unknown"}</span>
                              </li>
                              <li>
                                run digest{" "}
                                <span className="font-mono">
                                  {investigationHolder.digest
                                    ? investigationHolder.digest.slice(0, 16)
                                    : "unknown"}
                                </span>
                              </li>
                            </ul>
                          ) : (
                            <p className="text-muted-foreground">
                              This target is a live strategy, not a persisted encounter record. Its stored
                              runs and seed pairs are listed below.
                            </p>
                          )}
                          {investigationScenario != null ? (
                            <details className="mt-2 text-[11px]" data-investigation-scenario>
                              <summary className="cursor-pointer text-muted-foreground">
                                Frozen scenario
                              </summary>
                              <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/40 p-2">
                                {JSON.stringify(investigationScenario, null, 2)}
                              </pre>
                            </details>
                          ) : (
                            <p className="mt-2 text-[10px] text-muted-foreground">
                              The host returned no frozen scenario for this target.
                            </p>
                          )}
                        </div>
                        <div className="rounded-md border p-3 text-xs">
                          <p className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">
                            Provenance
                          </p>
                          <ul className="space-y-1">
                            <li>
                              library digest{" "}
                              <span className="break-all font-mono">{provenance?.digest ?? "unknown"}</span>
                            </li>
                            <li>
                              mode <span className="font-mono">{provenance?.mode ?? "unknown"}</span>
                            </li>
                            <li>
                              observed fights{" "}
                              <span className="font-mono">{formatInt(provenance?.count)}</span>
                            </li>
                          </ul>
                          <p className="mt-1 text-[10px] text-muted-foreground">
                            Replay reproduces the exact record on its own seed pair; it is not a fresh
                            resimulation of the build.
                          </p>
                        </div>
                      </div>

                      <EncounterEvidenceReadings windows={investigationDetail.encounterLedger?.windows ?? []} />

                      <div data-investigation-summary>
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Legacy stored bank and fresh trials
                        </p>
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Bank</TableHead>
                              <TableHead>attempts</TableHead>
                              <TableHead>retained</TableHead>
                              <TableHead>wins</TableHead>
                              <TableHead>losses</TableHead>
                              <TableHead>no verdict</TableHead>
                              <TableHead>win rate</TableHead>
                              <TableHead>mean earned</TableHead>
                              <TableHead>mean potential · loss-only</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            <InvestigationBankRow
                              label="Legacy stored"
                              dataBank="stored"
                              summary={investigationDetail.storedSummary}
                            />
                            <InvestigationBankRow
                              label="Fresh trials"
                              dataBank="trial"
                              summary={investigationDetail.trialSummary}
                            />
                          </TableBody>
                        </Table>
                        <p className="mt-1 text-[10px] text-muted-foreground">
                          Every figure is the host's own summary. Attempts is the lifetime total;
                          outcome counts and means use recorded totals, while replay examples may
                          roll off retention. Each mean shows its own measured sample count;
                          missing historical readings are not reconstructed. An unmeasured metric reads as
                          unknown, and mean potential counts defeats only as an opportunity, never a
                          yield.
                        </p>
                      </div>


                      <div className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Stored runs — every seed, digest and replay
                        </p>
                        <InvestigationRunTable
                          runs={storedRuns}
                          canReplay={canReplayInvestigation}
                          busy={simulationRunning}
                          onReplay={(run) => void replayInvestigationRun(run)}
                        />
                      </div>

                      <div className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Cumulative fresh trials — every trial kept for this build
                        </p>
                        <InvestigationRunTable
                          runs={trialRuns}
                          canReplay={canReplayInvestigation}
                          busy={simulationRunning}
                          onReplay={(run) => void replayInvestigationRun(run)}
                        />
                      </div>

                        </div>
                      </details>
                    </>
                  ) : null}
                </CardContent>
              </Card>
            ) : null}


            {/*
              Part J: the lane objective is a different objective, so a library written under the old
              single score says so, and is upgraded only when asked. The wording is explicit about what
              is reused and what cannot be reconstructed.
            */}
            {statusError ? (
              <Alert variant="destructive" data-optimizer-host-error>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>{status?.statusTransport?.lastError
                  ? "The desktop host could not refresh status"
                  : "The desktop host did not answer"}</AlertTitle>
                <AlertDescription>
                  <p className="font-mono text-[11px]">{statusError}</p>
                </AlertDescription>
              </Alert>
            ) : null}

            {status?.error ? (
              <Alert data-optimizer-message>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>Optimiser message</AlertTitle>
                <AlertDescription>
                  <p>{status.error}</p>
                </AlertDescription>
              </Alert>
            ) : null}

            {actionError ? (
              <Alert variant="destructive" data-optimizer-action-error>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>Command refused</AlertTitle>
                <AlertDescription>
                  <p>{actionError}</p>
                </AlertDescription>
              </Alert>
            ) : null}

            {notice ? (
              <p className="text-xs text-muted-foreground" data-optimizer-notice>
                {notice}
              </p>
            ) : null}

            {!api ? (
              <Card data-optimizer-no-host>
                <CardHeader>
                  <CardTitle className="text-base">Desktop host not connected</CardTitle>
                  <CardDescription>
                    `window.pywebview.api` is not present, so there is nothing to drive. Open this page
                    through the local desktop host rather than a plain browser tab.
                  </CardDescription>
                </CardHeader>
              </Card>
            ) : null}

            <div data-optimizer-explore><EncounterOptimizerPanel
              state={encounterAware}
              sendCommand={sendCommand}
              disabled={!api || busy !== null}
              paused={Boolean(status && status.state !== "Running" && status.state !== "Saving")}
              encounterOptions={encounterChoices}
            /></div>

            {(status?.totalRuns ?? 0) > 0 ? (
              <details className="rounded-lg border p-4" data-student-legacy-history>
                <summary className="cursor-pointer text-sm font-medium">Previous search history (read-only)</summary>
                <p className="mt-2 text-xs text-muted-foreground">
                  These records describe earlier searches. Community search now uses the budget and
                  experiment controls above; the former student percentages and stat tracks are retired.
                </p>
                <Button className="mt-3" size="sm" variant="outline" disabled={!api || busy !== null}
                  onClick={() => void sendCommand("Historical counts", "student_report", {})}>
                  Refresh historical counts
                </Button>
                <div className="mt-3 space-y-3">{studentHistoryBlock}</div>
              </details>
            ) : null}

            <details className="rounded-lg border p-4" data-legacy-search-diagnostics>
              <summary className="cursor-pointer text-sm font-medium">Previous search diagnostics (read-only)</summary>
              <p className="my-3 text-xs text-muted-foreground">Saved investigations remain available here.
                New refinement, boundary tests, support repair and confirmation use the Community budget above.</p>
              <fieldset disabled className="space-y-4" aria-label="Previous search diagnostics">
            {/*
              Breakthrough / reliability. Its own compute share (0% by default) and its own record: what
              it currently believes, what it changed to test that, what the change did, and whether the
              effect survived fresh seeds. Nothing here is a leaderboard: a maximum is labelled as a
              maximum, the archive is labelled as selected examples, and every probability is shown
              with the denominator it was measured over.
            */}
            {status?.breakthrough ? (
              <Card data-optimizer-breakthrough>
                <CardHeader>
                  <CardTitle className="text-base">Breakthrough / reliability</CardTitle>
                  <CardDescription>
                    A mechanism-guided investigation of this encounter&apos;s own runs, at any rank: it
                    contrasts a build&apos;s productive battles with its ordinary ones, proposes what
                    separates them, changes the build to make that behaviour more frequent, and only
                    claims an improvement that survives seeds it never used to choose it. Mean earned
                    stays the judge; the stage counters only decide what to test next.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-xs">
                  {Object.keys(status.breakthrough.encounters ?? {}).length === 0 ? (
                    <p className="text-muted-foreground" data-breakthrough-idle>
                      No investigation from the previous search is recorded for this library.
                    </p>
                  ) : (
                    Object.entries(status.breakthrough.encounters ?? {}).map(([encounter, entry]) => {
                      const stats = entry.stats ?? {};
                      const plan = entry.plan ?? {};
                      const hypothesis = entry.hypothesis ?? {};
                      const thresholds = Object.entries(stats.thresholds ?? {});
                      return (
                        <div key={encounter} className="space-y-2 rounded-md border p-3"
                             data-breakthrough-encounter={encounter}>
                          <div className="flex flex-wrap items-baseline gap-2">
                            <span className="font-medium">
                              {entry.label ?? `Encounter ${encounter}`}
                            </span>
                            <span className="rounded border px-1.5 py-0.5 text-[10px] uppercase"
                                  data-breakthrough-status={entry.status}>
                              {(entry.status ?? "unknown").replace(/_/g, " ")}
                            </span>
                            <span className="text-muted-foreground">
                              {entry.inFlight ? "experiment in flight" : "no experiment in flight"} ·
                              campaign {formatInt(entry.campaign?.used ?? entry.budget?.runs ?? 0)}
                              {" / "}{formatInt(entry.campaign?.cap ?? 0)} runs
                              {entry.campaign
                                ? ` · ${formatInt(entry.campaign.reserved ?? 0)} reserved · ` +
                                  `${formatInt(entry.campaign.remaining ?? 0)} remaining`
                                : ""}
                            </span>
                          </div>
                          {entry.campaign?.stopReason ? (
                            <p className="text-[11px] text-muted-foreground" data-breakthrough-budget-stop>
                              Paused for budget ({entry.campaign.stopReason}) in this encounter&apos;s own
                              campaign — this is a pause, not a verdict that no better build exists.
                              {entry.campaign.overCommitted
                                ? " Its in-flight plans are already committed beyond the cap; they are allowed to finish."
                                : ""}
                            </p>
                          ) : null}
                          {entry.campaign ? (
                            <div className="flex flex-wrap items-center gap-2" data-breakthrough-campaign>
                              <span className="text-[11px] text-muted-foreground">
                                Cap scope: {entry.campaign.scope ?? "one encounter"}
                                {entry.campaign.extension ? ` · ${entry.campaign.extension}` : ""}
                                {entry.campaign.unattributedRuns
                                  ? ` · ${formatInt(entry.campaign.unattributedRuns)} participant runs not attributable to this campaign, and not charged`
                                  : ""}
                              </span>
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                disabled={busy !== null || encounterAwareActive}
                                data-action="extend-breakthrough-budget"
                                onClick={() =>
                                  void sendCommand(
                                    `Campaign extension for encounter ${encounter}`,
                                    "breakthrough_budget",
                                    { encounterId: Number(encounter), runs: 2000 },
                                  )
                                }
                              >
                                Extend by {formatInt(2000)} runs and resume
                              </Button>
                            </div>
                          ) : null}
                          <p className="tabular-nums text-muted-foreground">
                            mean {formatMetric(stats.mean)} · median {formatMetric(stats.median)} ·
                            p10 {formatMetric(stats.p10)} · max {formatMetric(stats.maximum)}
                            {" "}(a maximum, not a reliability) · n={formatInt(stats.n ?? 0)}
                            {stats.unresolved ? ` · ${formatInt(stats.unresolved)} unresolved` : ""}
                          </p>
                          {thresholds.length ? (
                            <p className="tabular-nums text-muted-foreground">
                              {thresholds.map(([threshold, hit]) =>
                                `${threshold}+ ${formatInt(hit.hits ?? 0)}/${formatInt(hit.n ?? 0)}` +
                                (hit.rate == null ? "" : ` (${Math.round(hit.rate * 100)}%)`),
                              ).join(" · ")}
                            </p>
                          ) : null}
                          <p className="text-muted-foreground">
                            evidence: {formatInt(entry.coverage?.records ?? 0)} runs from{" "}
                            {formatInt(entry.coverage?.candidates ?? 0)} builds ·{" "}
                            {formatInt(entry.coverage?.withTelemetry ?? 0)} with behaviour detail
                            {entry.coverage?.withoutDetail
                              ? ` · ${formatInt(entry.coverage.withoutDetail)} counted but without detail`
                              : ""}
                            {entry.examples?.length
                              ? ` · ${formatInt(entry.examples.length)} selected examples (not a probability sample)`
                              : ""}
                          </p>
                          {hypothesis.statement ? (
                            <div className="space-y-1 rounded border-l-2 pl-2"
                                 data-breakthrough-hypothesis={hypothesis.id}>
                              <p className="text-[11px]">
                                <span className="font-medium">Hypothesis</span>{" "}
                                <span className="text-muted-foreground">
                                  ({hypothesis.status}
                                  {hypothesis.basis === "declared_prior"
                                    ? " · declared, not yet measured"
                                    : hypothesis.features?.length
                                      ? ` · separating: ${hypothesis.features.join(", ")}`
                                      : ""}
                                  {hypothesis.bottleneck ? ` · stage: ${hypothesis.bottleneck}` : ""})
                                </span>
                              </p>
                              <p>{hypothesis.statement}</p>
                              <p className="text-[11px] text-muted-foreground">
                                supporting:{" "}
                                {(hypothesis.supporting ?? []).map((run) =>
                                  `${run.label ?? String(run.candidate ?? "").slice(0, 8)} ${run.earned}ch`,
                                ).join(", ") || "none yet"}
                                {" · "}contrasting:{" "}
                                {(hypothesis.contrasting ?? []).map((run) =>
                                  `${run.label ?? String(run.candidate ?? "").slice(0, 8)} ${run.earned}ch`,
                                ).join(", ") || "none yet"}
                              </p>
                              <p className="text-[11px] text-muted-foreground">
                                would disprove it: {hypothesis.falsifier}
                              </p>
                            </div>
                          ) : null}
                          {plan.id ? (
                            <div className="space-y-1" data-breakthrough-plan={plan.id}>
                              <p className="text-[11px]">
                                <span className="font-medium">
                                  {plan.kind === "confirmation" ? "Confirmation" : "Controlled batch"}
                                </span>{" "}
                                <span className="text-muted-foreground">
                                  {plan.status} · {formatInt(plan.bank ?? 0)} runs per arm
                                  {plan.kind === "confirmation" && plan.developmentBank
                                    ? ` (fresh window from ${formatInt(plan.developmentBank)})`
                                    : ""}
                                </span>
                              </p>
                              {(plan.arms ?? []).map((arm) => (
                                <p key={arm.name} className="tabular-nums text-[11px]">
                                  <span className="font-medium">{arm.name}</span> {arm.role}
                                  {arm.axis ? ` · ${arm.axis} = ${JSON.stringify(arm.value)}` : ""}
                                  {arm.delta
                                    ? ` · paired ${arm.delta.mean == null ? "—" : (arm.delta.mean > 0 ? "+" : "") + formatNumber(arm.delta.mean, 2)}` +
                                      ` [${formatNumber(arm.delta.lower, 2)}, ${formatNumber(arm.delta.upper, 2)}] n=${formatInt(arm.delta.n ?? 0)}`
                                    : " · awaiting runs"}
                                  {arm.rewardPromising ? " · nominated" : ""}
                                </p>
                              ))}
                            </div>
                          ) : null}
                          {(entry.confirmed ?? []).length ? (
                            <p className="text-[11px]" data-breakthrough-confirmed>
                              confirmed on fresh seeds:{" "}
                              {(entry.confirmed ?? []).map((item) =>
                                `${String(item.candidate ?? "").slice(0, 10)} (+${formatNumber(item.delta?.mean, 2)} chests/battle, n=${formatInt(item.delta?.n ?? 0)})`,
                              ).join(" · ")}
                            </p>
                          ) : null}
                          {entry.claimsRevalidation?.needsRevalidation ? (
                            <p className="text-[11px] text-muted-foreground"
                               data-breakthrough-revalidation>
                              {formatInt(entry.claimsRevalidation.needsRevalidation)} earlier claim(s)
                              withdrawn from the badge and from selection: they cannot show the frozen
                              window evidence a confirmation now has to carry. The runs and the claim
                              itself are kept.
                              {(entry.claimsRevalidation.withdrawn ?? []).length
                                ? ` First: ${entry.claimsRevalidation.withdrawn
                                    ?.map((item) => `${String(item.candidate ?? "").slice(0, 10)} — ${item.reason}`)
                                    .join(" · ")}`
                                : ""}
                            </p>
                          ) : null}
                          <p className="text-[11px] text-muted-foreground">
                            last decision: {entry.lastDecision ?? "—"}
                          </p>
                        </div>
                      );
                    })
                  )}
                </CardContent>
              </Card>
            ) : null}

            {/*
              MP recovery. A losing build whose healer ran out of MP is the condition the user reported
              watching by hand; this card is where the optimiser shows that it noticed, whether it was
              allowed to test an item-supported build, and what that test measured.

              The card carries **two different scopes** and says so, because the first version did not
              and read as a per-strategy control: the *allowance* is library-wide - one item quantity
              for the whole library, applying to every build on every encounter whatever created it -
              while each *correction* below it is one build's own child with its own identity,
              statistics and bank. The allowance is the user's decision, so the card states plainly
              when a retry is waiting on an item quantity rather than leaving a silent no-op.
            */}
            {status?.mpRecovery ? (
              <Card data-optimizer-mp-recovery>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">
                    MP recovery (Holy Herb) <span className="text-muted-foreground">· library-wide setting</span>
                  </CardTitle>
                  <CardDescription>
                    <span data-mp-recovery-scope>
                      One allowance for this whole library: it applies to every build on every encounter,
                      whichever student or stream created it. The allowance itself is not per strategy —
                      what it authorises is, because each eligible parent gets its own herb-enabled
                      child with its own identity, statistics and bank.
                    </span>{" "}
                    Watches the role-resolved DPS and healer on every build&apos;s own runs and reports
                    when one reaches {formatInt(status.mpRecovery.thresholdPercent ?? 3)}% of its own
                    maximum MP during the battle. With an item allowance configured it also tests an
                    herb-supported child, measured on its own bank and reported separately from its
                    parent: recovering MP is not by itself an improvement.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-xs">
                  <div className="flex flex-wrap items-center gap-3" data-mp-recovery-setting>
                    <label className="flex items-center gap-2">
                      <Checkbox
                        checked={mpRecoveryDraft.enabled}
                        onCheckedChange={(checked) =>
                          setMpRecoveryDraft((draft) => ({ ...draft, enabled: checked === true }))
                        }
                        disabled={encounterAwareActive}
                        data-mp-recovery-enabled
                      />
                      Allow automatic MP-recovery experiments (all encounters, all builds)
                    </label>
                    {([
                      ["stock", "Holy Herb stock"],
                      ["maxUses", "Max uses per battle"],
                      ["bank", "Runs per comparison"],
                      ["maxChildren", "Most children at once"],
                    ] as const).map(([field, label]) => (
                      <label key={field} className="flex items-center gap-2">
                        <span className="text-muted-foreground">{label}</span>
                        <Input
                          type="number"
                          min={0}
                          className="h-7 w-24"
                          value={String(mpRecoveryDraft[field])}
                          disabled={encounterAwareActive}
                          data-mp-recovery-field={field}
                          onChange={(event) =>
                            setMpRecoveryDraft((draft) => ({
                              ...draft,
                              [field]: Math.max(0, Number(event.target.value) || 0),
                            }))
                          }
                        />
                      </label>
                    ))}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy !== null || encounterAwareActive}
                      data-action="save-mp-recovery"
                      onClick={() =>
                        void sendCommand("MP-recovery allowance", "mp_recovery", {
                          enabled: mpRecoveryDraft.enabled,
                          stock: mpRecoveryDraft.stock,
                          maxUses: mpRecoveryDraft.maxUses,
                          bank: mpRecoveryDraft.bank,
                          maxChildren: mpRecoveryDraft.maxChildren,
                        })
                      }
                    >
                      Save allowance
                    </Button>
                    <span className="text-muted-foreground" data-mp-recovery-state>
                      {status.mpRecovery.setting?.effective
                        ? `configured: stock ${formatInt(status.mpRecovery.setting?.stock ?? 0)}, `
                          + `max ${formatInt(status.mpRecovery.setting?.maxUses ?? 0)} uses per battle, `
                          + `bank ${formatInt(status.mpRecovery.setting?.bank ?? 0)}`
                        : "no item allowance configured — observation runs, but no herb-supported build is created"}
                      {typeof status.mpRecovery.scanBudgetSeconds === "number"
                        ? ` · scan budget ${formatNumber(status.mpRecovery.scanBudgetSeconds, 2)}s per pass`
                        : " · scan budget not published by this host build"}
                    </span>
                  </div>
                  {encounterAwareActive ? (
                    <p className="text-[11px] text-muted-foreground" data-mp-recovery-encounter-block>
                      The MP-recovery allowance is read-only while the encounter-aware mode is active; its
                      support questions (HP / MP / DEF) replace these independent MP-recovery streams.
                    </p>
                  ) : null}
                  {status.mpRecovery.awaitingAllowance ? (
                    <p className="text-muted-foreground" data-mp-recovery-awaiting>
                      Low MP detected; herb-supported retry awaits item allowance.
                    </p>
                  ) : null}
                  {(status.mpRecovery.requests ?? []).length === 0 ? (
                    <p className="text-muted-foreground" data-mp-recovery-idle>
                      No MP-recovery correction has been requested yet. Builds are watched on their own
                      runs; a candidate is only corrected when a watched unit crossed the threshold
                      before its battle ended.
                    </p>
                  ) : (
                    (status.mpRecovery.requests ?? []).map((request) => (
                      <div key={request.request} className="space-y-1 rounded-md border p-3"
                           data-mp-recovery-request={request.request}>
                        <div className="flex flex-wrap items-baseline gap-2">
                          <span className="font-medium">
                            {request.crossing
                              ? `${request.crossing.unit} reached ${formatInt(request.crossing.minimumMpPercent ?? 0)}% MP at tick ${formatInt(request.crossing.tick ?? 0)} (run ended tick ${formatInt(request.crossing.runEnd ?? 0)})`
                              : "threshold crossing recorded"}
                          </span>
                          <span className="rounded border px-1.5 py-0.5 text-[10px] uppercase"
                                data-mp-recovery-status={request.status}>
                            {(request.status ?? "unknown").replace(/_/g, " ")}
                          </span>
                        </div>
                        <p className="text-muted-foreground">
                          parent {String(request.parentId ?? "").slice(0, 10)} · child{" "}
                          {String(request.childId ?? "").slice(0, 10)} · policy stock{" "}
                          {formatInt(request.policy?.holyHerbStock ?? 0)} / max{" "}
                          {formatInt(request.policy?.holyHerbMaxUses ?? 0)} · watching{" "}
                          {(request.policy?.holyHerbTriggerUnits ?? []).join(", ") || "—"}
                        </p>
                        <p className="tabular-nums text-muted-foreground">
                          parent: {formatInt(request.parent?.attempts ?? 0)} runs · mean{" "}
                          {formatMetric(request.parent?.meanEarned ?? null)} ·{" "}
                          {formatInt(request.parent?.wins ?? 0)}W/{formatInt(request.parent?.losses ?? 0)}L
                          {" "}· herbs used {formatInt(request.parent?.herbUses ?? 0)}
                        </p>
                        <p className="tabular-nums text-muted-foreground">
                          child: {formatInt(request.child?.attempts ?? 0)} runs · mean{" "}
                          {formatMetric(request.child?.meanEarned ?? null)} ·{" "}
                          {formatInt(request.child?.wins ?? 0)}W/{formatInt(request.child?.losses ?? 0)}L
                          {" "}· herbs used {formatInt(request.child?.herbUses ?? 0)} in{" "}
                          {formatInt(request.child?.herbUseRuns ?? 0)} of{" "}
                          {formatInt(request.child?.herbRuns ?? 0)} runs
                        </p>
                        <p className="text-muted-foreground">
                          bank {formatInt(request.observed ?? 0)}/{formatInt(request.bank ?? 0)}
                          {request.reserved ? ` · ${formatInt(request.reserved)} reserved` : ""}
                        </p>
                        <p className="text-[11px] text-muted-foreground">
                          {request.decision ?? "—"}
                        </p>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            ) : null}

              </fieldset>
            </details>

            {/*
              Optional exploration. Everything below is a diagnostic or a table: the discovery lanes,
              the objective/outcome readings, the strategy families, the run-ledger cards, the
              threshold probes and the search settings. It is closed by default so the selected
              strategy's own workspace stays the primary reading flow, and nothing here is removed —
              every feature below is one click away.
            */}
            <details className="rounded-lg border p-4" data-optimizer-explore>
              <summary className="cursor-pointer select-none text-sm font-semibold">
                Explore further — discovery lanes, strategy families, run tables, thresholds &amp;
                search settings
              </summary>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Optional diagnostics. Numbers here are the host's own readings; a caveat that applies
                is stated beside the number it applies to.
              </p>
              <div className="mt-4 space-y-4">
            {/*
              The lane objective, in the open. Four independent leaders for the focused fight, each
              answering a different question about the same population. This is a *discovery parent*
              panel, not a recommendation: the validated flag and win reading say how much evidence
              stands behind the entry, and a lane leader is allowed to have none of it yet.
            */}
            {fightKey && laneLeaders.length ? (
              <Card data-optimizer-lanes>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Layers className="h-4 w-4" /> Discovery lanes
                    {fightLabel ? (
                      <span className="text-xs font-normal text-muted-foreground">
                        {fightLabel.title}
                      </span>
                    ) : null}
                  </CardTitle>
                  <CardDescription>
                    Each lane keeps the best build by its own signal, so none of them can be erased by
                    another. A lane leader is a promising <span className="text-foreground">discovery
                    parent</span> - something worth mutating further - which is not the same claim as a
                    <span className="text-foreground"> reliable strategy</span>: that reading is the
                    win rate and the validated mark beside it.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {laneLeaders.map((entry) => (
                    <div
                      key={entry.lane}
                      className={cn(
                        "flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2",
                        entry.leader.candidate === selectedId && "border-primary/50 bg-primary/5",
                      )}
                      data-lane={entry.lane}
                      data-lane-leader={entry.leader.candidate}
                    >
                      <div className="min-w-0 space-y-0.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-semibold">{entry.title}</span>
                          <span className="truncate font-mono text-[11px] text-muted-foreground" title={entry.leader.label}>
                            {entry.leader.label}
                          </span>
                          {entry.leader.validated ? (
                            <Badge variant="secondary" data-lane-validated="true">validated</Badge>
                          ) : (
                            <Badge variant="outline" data-lane-validated="false">provisional</Badge>
                          )}
                        </div>
                        <div className="text-[11px] text-muted-foreground" data-lane-metric>
                          {entry.reading}
                        </div>
                        <div className="font-mono text-[10px] tabular-nums text-muted-foreground" data-lane-evidence>
                          {formatInt(entry.leader.metrics.runs)} runs · win{" "}
                          {formatPercent(entry.leader.metrics.winRate)} (95% L{" "}
                          {formatNumber(entry.leader.metrics.winLower, 2)}) ·{" "}
                          {formatNumber(entry.leader.metrics.resources, 2)} consumables/battle ·{" "}
                          {entry.leader.pool} in lane
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                          {entry.purpose}
                        </span>
                        <Button
                          type="button" size="sm"
                          variant={entry.leader.candidate === selectedId ? "default" : "outline"}
                          onClick={() => {
                            setSelectedId(entry.leader.candidate);
                            setFamilyKey(null);
                            setExampleLabel(null);
                          }}
                          data-action="select-lane-leader"
                          data-lane-select={entry.lane}
                          title="Show this build in the strategy detail cards"
                        >
                          {entry.leader.candidate === selectedId ? "Selected" : "Inspect"}
                        </Button>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            ) : null}
            <section className="space-y-4" data-optimizer-results>
              <Card data-optimizer-outcome>
                <CardHeader className="pb-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">
                        {headlineRetained.known
                          ? "Chests kept by the strategy below"
                          : headlineChests ? "Chests per run for the strategy below" : "Chests"}
                      </CardTitle>
                      {/*
                        Ownership first: the number under this title belongs to exactly one
                        strategy, and the page says which. Without that, "the big chest number"
                        could just as easily read as the library's best.
                      */}
                      <p className="mt-1 text-sm" data-optimizer-headline-owner>
                        <span className="font-medium text-foreground">
                          {selected ? "Selected strategy" : "Best strategy so far (nothing selected)"}
                        </span>{" "}
                        <span className="font-mono text-xs">{headline?.label ?? "—"}</span>
                        {headline ? (
                          <span className="text-muted-foreground">
                            {" · "}
                            {encounterLabel(encounterIdOf(headline), encounters?.[String(encounterIdOf(headline))])}
                          </span>
                        ) : null}
                      </p>
                      <CardDescription>
                        {headlineRetained.known
                          ? "Mean chests kept per run, straight from the runner's retained samples."
                          : "Mean chests per resolved run: 0 when the fight is lost, because the recorded native gate dispatches no chest on a loss, and the awarded or queued count when it is won."}
                      </CardDescription>
                    </div>
                    <ToneBadge category={headlineRetained.known || headlineChests ? "success" : "warning"}>
                      {headlineRetained.known
                        ? "retained chests measured"
                        : headlineChests ? "chests counted at the gate" : "no resolved run yet"}
                    </ToneBadge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
                    {headlineRetained.known ? (
                      <>
                        <span className="font-mono text-4xl font-semibold tabular-nums" data-retained-outcome>
                          {formatNumber(headlineRetained.mean, 2)}
                        </span>
                        <span className="text-sm text-muted-foreground">
                          retained chests per run
                          {headlineRetained.samples == null
                            ? ""
                            : ` · ${formatInt(headlineRetained.samples)} sampled run(s)`}
                        </span>
                      </>
                    ) : headlineChests ? (
                      <>
                        <span className="font-mono text-4xl font-semibold tabular-nums" data-retained-outcome>
                          {formatNumber(headlineChests.mean, 2)}
                        </span>
                        <span className="text-sm text-muted-foreground">
                          chests per run
                          {headlineChests.samples == null
                            ? ""
                            : ` · ${formatInt(headlineChests.samples)} resolved ${SCOPE_LABEL[headlineChests.scope]}`}
                          {" · min "}
                          {formatInt(headline?.selection?.chestMin)}
                          {" max "}
                          {formatInt(headline?.selection?.chestMax)}
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="text-2xl font-semibold text-muted-foreground" data-retained-outcome>
                          No resolved run yet
                        </span>
                        <span className="text-sm text-muted-foreground">
                          every completed fight reports its chests here
                        </span>
                      </>
                    )}
                    {/*
                      The per-run cards above already say what each run awarded, with its basis. An
                      aggregate "awarded" total across a handful of stored examples is not a yield
                      and reads like one, so it is deliberately not shown next to a chest figure.
                    */}
                  </div>
                  <p className="text-xs text-muted-foreground" data-optimizer-caveat>
                    {headlineRetained.known
                      ? "These means come from the runner's own retained samples - a simulator measurement, not a real-game guarantee. An awarded count is an entitlement, not an inventory receipt, and prize callbacks count combat events only."
                      : "Chests are counted at the recorded native gate: a loss dispatches none, so it counts 0 however many prizes the fight queued; a win counts the certified award, or the queued count at the verdict when no certificate holds. This is what the fight released, and it is not yet proof a chest reached your inventory - automatic Finish and world collection remain unproven - so it is the optimiser's objective, not a retained-inventory receipt. Prize callbacks are combat activity and are kept only as a diagnostic. Results stay provisional, and supplied stats are not proof a build is achievable in-game."}
                  </p>
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      What is known instead
                    </p>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <StatTile
                        label="Best win rate"
                        value={formatPercent(headlineWin?.rate)}
                        hint={`95% lower bound ${formatPercent(headlineWin?.lower)} · ${headlineWin ? SCOPE_LABEL[headlineWin.scope] : "no runs yet"}`}
                      />
                      <StatTile
                        label="Wins observed"
                        value={`${formatInt(headlineWin?.wins)}/${formatInt(headlineWin?.samples)}`}
                        hint={`best-ranked strategy · ${headlineWin ? SCOPE_LABEL[headlineWin.scope] : "no runs yet"}`}
                      />
                      <StatTile
                        label="No verdict by limit"
                        value={formatPercent(headline?.selection?.unresolvedRate)}
                        hint={`battle still unresolved when the simulation safety limit was reached · ${formatCount(horizonTicks)} ticks · ${formatTicksClock(horizonTicks)}`}
                      />
                      <StatTile label="Runs recorded" value={formatInt(status?.totalRuns)} hint="whole library" />
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card data-optimizer-best-builds data-strategy-families>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Layers className="h-4 w-4" />
                    Strategy families
                  </CardTitle>
                  <p className="text-xs font-medium text-foreground/90" data-family-fight-scope>
                    {fightLabel
                      ? `${fightLabel.encounter} · ${fightLabel.difficulty}`
                      : "Every fight in this library - pick a difficulty in the Encounter overview to scope this list"}
                  </p>
                  <CardDescription>
                    What genuinely different approaches has this optimiser found for this fight? One row
                    per family, not per emitted mutation: a family shares the runner's behaviour cell and
                    the same set of differences from that encounter's supplied baseline (party, formation
                    slot, a unit's skill order and invocation levels, and the consumable plan — weapon,
                    equipment and supplied stat values are not part of this diff). Complete comparable
                    banks come first, ordered by average chests per run, then win rate. A highest single
                    award remains visible on each card but does not determine its rank. The numbers on a family card are that family's
                    own records; the Encounter overview holds the encounter-wide lifetime records across
                    every strategy tried for the fight.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-[11px] text-muted-foreground" data-optimizer-encounter-scope>
                    {encounters
                      ? `${formatInt(Object.keys(encounters).length)} encounters available in this build's data`
                      : "Encounter catalogue unavailable from the host"}
                    {" · "}
                    {encounterIds.length === 0
                      ? "no strategy has a recorded encounter yet"
                      : `${formatInt(encounterIds.length)} of them have strategies here`}
                  </p>
                  {encounterIds.length > 1 ? (
                    <div className="flex flex-wrap items-center gap-2 pb-1" data-optimizer-encounter-filter>
                      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                        Fight
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant={encounterFilter === "all" ? "default" : "outline"}
                        onClick={() => setEncounterFilter("all")}
                        data-encounter-chip="all"
                      >
                        All {formatInt(candidates.length)}
                      </Button>
                      {encounterIds.map((id) => (
                        <Button
                          key={id}
                          type="button"
                          size="sm"
                          variant={encounterFilter === String(id) ? "default" : "outline"}
                          onClick={() => setEncounterFilter(String(id))}
                          title={encounterLabel(id, encounters?.[String(id)])}
                          data-encounter-chip={id}
                        >
                          #{id} {formatInt(encountersById.get(id))}
                        </Button>
                      ))}
                    </div>
                  ) : null}
                  {candidates.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      No strategies in this library yet. Start the search or import a supplied build.
                    </p>
                  ) : null}
                  {candidates.length > 0 && visibleFamilies.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      No strategies for this fight yet. Pick another one, or keep searching.
                    </p>
                  ) : null}
                  {visibleFamilies.map((family, index) => {
                    const representative = family.representative;
                    const chests = family.representativeChests;
                    const win = family.representativeWin;
                    const active = family.members.some((member) => member.id === selectedId);
                    const familySelected = familyKey === family.key;
                    const expanded = expandedFamilies.includes(family.key);
                    const watchLabel = bestStoredRunLabel(representative);
                    const region = expanded ? regionFor(family.members, family.behaviour) : null;
                    const statCells = expanded ? fighterStatCells(representative.stats) : [];
                    const encounter = family.encounterId === null ? undefined : encounters?.[String(family.encounterId)];
                    const scope = win ? ` · ${SCOPE_LABEL[win.scope]}` : " · no runs yet";
                    const winReading = `win ${formatPercent(win?.rate)} · 95% CI [${formatInterval(family.representativeInterval)}]${scope}`;
                    return (
                      <div
                        key={family.key}
                        className={cn("rounded-md border", active && "border-primary/50 bg-primary/5")}
                        data-strategy-family={family.key}
                        data-family-state={family.tested ? "sufficient" : "provisional"}
                        data-family-representative={representative.id}
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3 px-3 py-2">
                          <div className="min-w-0 space-y-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                                #{index + 1}
                              </span>
                              <span className="text-sm font-medium" data-family-title={family.title}>
                                {family.title}
                              </span>
                              <Badge
                                variant="outline"
                                className="text-[10px]"
                                data-family-members={formatInt(family.members.length)}
                              >
                                {formatInt(family.members.length)} member{family.members.length === 1 ? "" : "s"}
                              </Badge>
                              <ToneBadge category={family.tested ? "success" : "warning"}>
                                {family.tested ? "sufficiently tested" : "provisional"}
                              </ToneBadge>
                            </div>
                            <div
                              className="text-[10px]"
                              data-family-encounter={encounterLabel(family.encounterId, encounter)}
                            >
                              <span className="font-medium text-foreground/80">
                                {encounterLabel(family.encounterId, encounter)}
                              </span>
                              <span className="text-muted-foreground">
                                {" · "}
                                {familyDetail(family.behaviour)} · representative {representative.label}
                              </span>
                            </div>
                            <p className="text-[11px]" data-family-difference={family.difference}>
                              <span className="mr-1 text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
                                Different because
                              </span>
                              {family.difference}
                            </p>
                            {/* The family's own records and work, never the encounter-wide records. */}
                            <div
                              className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-[10px] text-muted-foreground"
                              data-family-metrics
                            >
                              <span data-family-earned>
                                best chests earned{" "}
                                <span className="font-mono text-xs text-foreground">
                                  {family.metrics.earned == null ? "—" : formatInt(family.metrics.earned)}
                                </span>
                              </span>
                              <span data-family-potential>
                                highest potential{" "}
                                <span className="font-mono text-xs text-foreground">
                                  {family.metrics.potential == null
                                    ? "—"
                                    : formatInt(family.metrics.potential)}
                                </span>
                              </span>
                              <span data-family-attempts>
                                attempts{" "}
                                <span className="font-mono text-xs text-foreground">
                                  {formatInt(family.metrics.attempts)}
                                </span>
                              </span>
                              <span data-family-winrate>
                                win rate{" "}
                                <span className="font-mono text-xs text-foreground">
                                  {formatPercent(family.metrics.winRate)}
                                </span>
                              </span>
                              <span data-family-noverdict>
                                no verdict by limit{" "}
                                <span className="font-mono text-xs text-foreground">
                                  {formatInt(family.metrics.noVerdict)}
                                </span>
                              </span>
                              <span data-family-consumables>
                                consumables{" "}
                                <span className="font-mono text-xs text-foreground">
                                  {family.metrics.consumableMembers
                                    ? `${formatInt(family.metrics.consumableMembers)}/${formatInt(family.members.length)}`
                                    : "none"}
                                </span>
                              </span>
                            </div>
                            <p
                              className="text-[10px] text-muted-foreground"
                              data-family-state-detail={family.stateDetail}
                            >
                              {family.stateDetail}
                            </p>
                          </div>
                          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
                            <span className="text-muted-foreground">chests / run</span>
                            <span
                              className={cn(
                                "font-mono text-lg font-semibold tabular-nums",
                                chests ? "text-foreground" : "text-muted-foreground",
                              )}
                              data-family-chests={chests ? formatNumber(chests.mean, 2) : "—"}
                              title={
                                chests?.se == null
                                  ? "Mean chests per resolved run."
                                  : `Mean chests per resolved run. Per-run counts vary widely, so this mean carries ±${formatNumber(1.96 * chests.se, 2)} at 95%.`
                              }
                            >
                              {chests ? formatNumber(chests.mean, 2) : "—"}
                            </span>
                            {chests ? (
                              <span className="text-[10px] text-muted-foreground">
                                ±{formatNumber(1.96 * (chests.se ?? 0), 2)} at 95% over{" "}
                                {formatInt(chests.samples)} {SCOPE_LABEL[chests.scope]}
                              </span>
                            ) : null}
                            {withinNoiseOfBest(chests) ? (
                              <Badge variant="outline" className="text-[10px]" data-family-within-noise="true">
                                within noise of the best
                              </Badge>
                            ) : null}
                            <span className="tabular-nums text-muted-foreground" data-family-win={winReading}>
                              {winReading}
                            </span>
                            <span className="text-muted-foreground">{strategyReliability(representative)}</span>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={() => watchFamilyRun(representative)}
                              disabled={!api || replayBlocked || !watchLabel || stateLabel === "Running" || stateLabel === "Saving"}
                              title={
                                watchLabel
                                  ? `Replay ${representative.label} · ${watchLabel}`
                                  : "This build has no stored run to watch yet"
                              }
                              data-action="watch-family-run"
                            >
                              <Swords className="mr-1 h-3.5 w-3.5" />
                              Watch best run
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant={familySelected ? "default" : "outline"}
                              onClick={() => selectFamily(family)}
                              data-action="select-family-representative"
                              data-family-selected={familySelected ? "true" : "false"}
                            >
                              {familySelected ? "Selected family" : "Select family"}
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={() => toggleFamily(family.key)}
                              aria-expanded={expanded}
                              data-action="toggle-family-members"
                              data-family-toggle={family.key}
                            >
                              <ChevronDown className={cn("mr-1 h-3.5 w-3.5 transition-transform", expanded && "rotate-180")} />
                              {expanded ? "Hide members" : `Members (${formatInt(family.members.length)})`}
                            </Button>
                          </div>
                        </div>
                        {expanded ? (
                          <div className="space-y-3 border-t px-3 py-2">
                            {family.members.length === 1 ? (
                              <p className="text-[10px] text-muted-foreground">
                                This family has exactly one member, so the row above is the whole family.
                              </p>
                            ) : null}
                            <div className="space-y-1" data-family-members-list={family.key}>
                              {family.members.map((member) => {
                                const memberChests = candidateChests(member);
                                const memberWin = candidateWin(member);
                                const memberActive = member.id === selectedId;
                                const memberWatch = bestStoredRunLabel(member);
                                const memberState = familyTestState(member);
                                const memberInterval = memberWin
                                  ? summaryForScope(member, memberWin.scope)?.winInterval
                                  : undefined;
                                return (
                                  <div
                                    key={member.id}
                                    className={cn(
                                      "flex flex-wrap items-center justify-between gap-3 rounded border px-2 py-1",
                                      memberActive && "border-primary/40 bg-primary/5",
                                    )}
                                    data-family-member={member.id}
                                  >
                                    <div className="min-w-40">
                                      <div className="text-xs font-medium">{member.label}</div>
                                      <div className="text-[10px] text-muted-foreground">
                                        {member.source} ·{" "}
                                        {differenceSentence(
                                          changeAspects(member, family.baseline),
                                          family.baseline,
                                          family.encounterId,
                                          member.id === family.baseline?.id,
                                        )}
                                      </div>
                                    </div>
                                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
                                      <span className="tabular-nums">
                                        chests / run <span className="font-mono">{memberChests ? formatNumber(memberChests.mean, 2) : "—"}</span>
                                        {memberChests
                                          ? ` ±${formatNumber(1.96 * (memberChests.se ?? 0), 2)} over ${formatInt(memberChests.samples)} ${SCOPE_LABEL[memberChests.scope]}`
                                          : ""}
                                      </span>
                                      {withinNoiseOfBest(memberChests) ? (
                                        <Badge variant="outline" className="text-[10px]" data-member-within-noise="true">
                                          within noise of the best
                                        </Badge>
                                      ) : null}
                                      <span className="tabular-nums text-muted-foreground">
                                        win {formatPercent(memberWin?.rate)} · 95% CI [{formatInterval(memberInterval)}]
                                      </span>
                                      <span className="text-muted-foreground">{strategyReliability(member)}</span>
                                      <ToneBadge category={memberState.tested ? "success" : "warning"}>
                                        {memberState.tested ? "tested" : "provisional"}
                                      </ToneBadge>
                                      <Button
                                        type="button"
                                        size="sm"
                                        variant="outline"
                                        onClick={() => watchFamilyRun(member)}
                                        disabled={!api || replayBlocked || !memberWatch || stateLabel === "Running" || stateLabel === "Saving"}
                                        title={memberWatch ? `Replay ${member.label} · ${memberWatch}` : "This build has no stored run to watch yet"}
                                        data-action="watch-family-member"
                                      >
                                        Watch
                                      </Button>
                                      <Button
                                        type="button"
                                        size="sm"
                                        variant={memberActive ? "default" : "outline"}
                                        onClick={() => setSelectedId(member.id)}
                                        data-action="select-family-member"
                                      >
                                        {memberActive ? "Selected" : "Inspect"}
                                      </Button>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                            <div className="space-y-2" data-family-region={family.key}>
                              <p className="text-xs font-medium">Representative's build and joint tested ranges</p>
                              <p
                                className="text-[11px] text-muted-foreground"
                                data-family-representative-build={representative.id}
                              >
                                Representative: <span className="font-medium text-foreground/90">{representative.label}</span>
                                {" — "}
                                {chests ? `${formatNumber(chests.mean, 2)} chests per run over ${formatInt(chests.samples)} ${SCOPE_LABEL[chests.scope]}` : "no resolved run yet"}
                                {" · "}
                                {family.stateDetail}
                              </p>
                              {statCells.length === 0 ? (
                                <p className="text-xs text-muted-foreground" data-family-build-empty>
                                  The runner supplied no prepared stat values for this build.
                                </p>
                              ) : (
                                <>
                                  <Table>
                                    <TableHeader>
                                      <TableRow>
                                        <TableHead>Stat (fighter · key)</TableHead>
                                        <TableHead className="text-right">Value</TableHead>
                                      </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                      {statCells.map((cell) => (
                                        <TableRow key={cell.key}>
                                          <TableCell className="text-[11px]">{cell.label}</TableCell>
                                          <TableCell className="text-right font-mono text-[11px] tabular-nums">
                                            {formatNumber(cell.value, 2)}
                                          </TableCell>
                                        </TableRow>
                                      ))}
                                    </TableBody>
                                  </Table>
                                  <p className="text-[10px] text-muted-foreground">
                                    Parameter ids are the runner's own numeric keys; no stat names are invented.
                                    These are supplied or canonically mutated builds, not an achievability claim.
                                    Weapons and reach: {weaponSummary(representative.stats)}
                                  </p>
                                </>
                              )}
                              {region === null || region.columns.length === 0 ? (
                                <p className="text-xs text-muted-foreground" data-family-region-empty>
                                  No build in this family has a complete {SELECTION_RUNS}-run selection bank with a
                                  lower win-interval bound ≥ {RECOMMENDED_WIN_LOW.toFixed(2)} yet, so the joint stat
                                  ranges are not available yet.
                                </p>
                              ) : (
                                <>
                                  <p className="text-xs text-muted-foreground" data-family-best-observed>
                                    Best observed callback mean in this family:{" "}
                                    <span className="font-mono text-foreground">{formatNumber(region.bestObserved, 3)}</span>.
                                    Best observed is the best of these tested builds only — it is never a global optimum.
                                  </p>
                                  <Table>
                                    <TableHeader>
                                      <TableRow>
                                        <TableHead className="min-w-48">Stat (fighter · key)</TableHead>
                                        {region.columns.map(({ candidate: column, recommended }) => (
                                          <TableHead key={column.id} className="text-right">
                                            <div className="font-mono text-[10px]">{shortId(column.id)}</div>
                                            <div className="text-[9px] font-normal text-muted-foreground">
                                              {recommended ? "recommended" : "viable"}
                                            </div>
                                          </TableHead>
                                        ))}
                                      </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                      {region.rows.map((row) => (
                                        <TableRow key={row.key}>
                                          <TableCell className="text-[11px]">{row.label}</TableCell>
                                          {row.values.map((value, valueIndex) => (
                                            <TableCell
                                              key={region.columns[valueIndex].candidate.id}
                                              className="text-right font-mono text-[11px] tabular-nums"
                                            >
                                              {formatNumber(value, 2)}
                                            </TableCell>
                                          ))}
                                        </TableRow>
                                      ))}
                                    </TableBody>
                                  </Table>
                                  <p className="text-[10px] text-muted-foreground">
                                    Only builds in this same measured family with a complete, comparable{" "}
                                    {SELECTION_RUNS}-run selection bank are shown. Recommended = win-interval lower
                                    bound ≥ {RECOMMENDED_WIN_LOW.toFixed(2)} and callback mean within{" "}
                                    {Math.round(RECOMMENDED_CALLBACK_SHARE * 100)}% of the best observed in the
                                    family. Each column is one tested joint build and the cells are its measured
                                    values — not independent per-stat ranges or min/max promises. No interpolation
                                    and no in-game achievability certification are implied.
                                  </p>
                                </>
                              )}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </CardContent>
              </Card>

              <Card data-optimizer-runs data-selected-candidate={selected?.id ?? ""}>
                <CardHeader className="pb-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">
                        Runs worth watching — {selectedFamily ? "Selected Strategy Family" : "Chosen Strategy"}
                      </CardTitle>
                      <CardDescription>
                        Real seed pairs the runner stored for{" "}
                        <span className="font-medium text-foreground/90">
                          {selectedFamily ? selectedFamily.title : selected ? selected.label : "the chosen build"}
                        </span>
                        {selectedFamily ? " — the family you selected above" : ""}. Every card below is
                        scoped to this one strategy family, not to the encounter's lifetime records: "most
                        chests" here means most chests <em>within this family</em>, which is why it can be
                        lower than the encounter-wide record in the overview above. Each card is one
                        battle: its outcome, the chests it released and what it consumed, and it can
                        replay that exact scenario and seed pair locally.
                      </CardDescription>
                      {/*
                        Which fight every card below is. The same scenario is reused for all of a
                        strategy's runs, so it is stated once here and repeated per card.
                      */}
                      <div className="mt-1 text-xs" data-optimizer-runs-encounter>
                        <span className="font-medium text-foreground/90">
                          {fightLabel
                            ? `${fightLabel.encounter}${fightLabel.difficulty ? ` · ${fightLabel.difficulty}` : ""}`
                            : encounterLabel(selectedEncounterId, selectedEncounter)}
                        </span>
                        {selectedEncounterRoster ? (
                          <span className="text-muted-foreground">
                            {" — "}
                            {selectedEncounterRoster}
                          </span>
                        ) : null}
                      </div>
                      {/*
                        Who fights. The party is part of the scenario, so it is identical for every run
                        of a strategy: stated once here rather than repeated on each card.
                      */}
                      <div className="mt-1 text-xs text-muted-foreground" data-optimizer-runs-party>
                        <span className="font-medium text-foreground/90">Allies</span>{" "}
                        {party.allies.length ? party.allies.join(", ") : "none recorded in this scenario"}
                        {" · "}
                        <span className="font-medium text-foreground/90">Pets</span>{" "}
                        {party.pets.length ? party.pets.join(", ") : "none in this build"}
                      </div>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => void runReplay()}
                      disabled={
                        !api || replayBlocked || !exampleLabel || stateLabel === "Running" || stateLabel === "Saving"
                      }
                      data-action="optimizer-replay"
                    >
                      <Swords className="mr-1 h-3.5 w-3.5" />
                      {replayBusy ? "Running replay…" : `Watch ${exampleLabel ?? "selected run"}`}
                    </Button>
                  </div>
                  {replayBusy ? (
                    <p className="text-xs text-muted-foreground" data-optimizer-replay-busy>
                      The runner is producing the exact trace for this seed pair; a replay can take a while.
                    </p>
                  ) : null}
                  {replayError ? (
                    <p className="text-xs text-destructive" data-optimizer-replay-error>
                      {replayError}
                    </p>
                  ) : null}
                </CardHeader>
                <CardContent className="space-y-3">
                  {!selected ? (
                    <p className="text-xs text-muted-foreground">
                      Select a strategy to see the exact runs it stored.
                    </p>
                  ) : null}
                  {selected && exampleLabels.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      This build has no stored runs yet, so there is nothing to watch.
                    </p>
                  ) : null}
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {exampleLabels.map((label) => {
                      const example = selected?.examples?.[label];
                      const outcome = runOutcome(example);
                      const award = awardReading(example);
                      const retainedCount = retainedRunValue(example);
                      const chest = chestReading(example);
                      const hasSeeds = Array.isArray(example?.seeds) && (example?.seeds?.length ?? 0) >= 2;
                      const active = label === exampleLabel;
                      return (
                        <div
                          key={label}
                          className={cn(
                            "flex flex-col gap-2 rounded-md border p-3",
                            active && "border-primary/50 bg-primary/5",
                          )}
                          data-run={label}
                          data-run-outcome={outcome}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            {/* The scope is in the label: a family's "most chests" is not the fight's record. */}
                            <span className="text-sm font-medium">
                              {label}
                              {selectedFamily ? (
                                <span className="text-muted-foreground"> — This Family</span>
                              ) : null}
                            </span>
                            <ToneBadge category={outcomeCategory(outcome)}>
                              {runReason(example, chest.known ? chest.count : null, exampleChestsMax)}
                            </ToneBadge>
                          </div>
                          <div className="text-[10px] text-muted-foreground" data-run-encounter>
                            {encounterLabel(selectedEncounterId, selectedEncounter)}
                          </div>
                          <div className="rounded-md bg-muted/40 px-3 py-2">
                            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                              Chests
                            </div>
                            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                              <span
                                className={cn(
                                  "font-mono text-3xl font-semibold tabular-nums",
                                  chest.known ? "text-foreground" : "text-muted-foreground",
                                )}
                                data-run-retained
                                data-run-chests={chest.known ? chest.count : "unknown"}
                              >
                                {chest.known ? formatInt(chest.count) : "Unknown"}
                              </span>
                              <span className="text-[10px] text-muted-foreground">
                                {chest.known
                                  ? chestBasisLabel(chest.basis)
                                  : chest.reason}
                              </span>
                            </div>
                          </div>
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
                            <span className="font-medium text-foreground/90" data-run-outcome-label>
                              {RUN_OUTCOME_LABEL[outcome]}
                            </span>
                            <span>·</span>
                            <span
                              className="font-mono"
                              data-run-award
                              data-run-award-basis={award.known ? award.basis : "unknown"}
                            >
                              {awardLabel(award)}
                            </span>
                            {retainedCount !== null ? (
                              <>
                                <span>·</span>
                                <span data-run-retained-sample>retained sample {formatInt(retainedCount)}</span>
                              </>
                            ) : null}
                          </div>
                          <dl className="space-y-1 text-xs text-muted-foreground">
                            <div className="flex items-center justify-between gap-2">
                              <dt>Consumables used</dt>
                              <dd className="font-mono" data-run-consumables>
                                {formatInt(example?.resourceUses)}
                              </dd>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                              <dt>Pending award (queue)</dt>
                              <dd className="font-mono" data-run-pending>
                                {award.pending === null ? "Unknown" : formatInt(award.pending)}
                              </dd>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                              <dt>Prize callbacks</dt>
                              <dd className="font-mono" data-run-callbacks>
                                {formatInt(example?.prizeCallbacks)}
                              </dd>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                              <dt>Survivors · ticks</dt>
                              <dd className="font-mono">
                                {formatInt(example?.survivors)} · {formatInt(example?.ticks)}
                              </dd>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                              <dt>Seeds [math, lib]</dt>
                              <dd className="font-mono text-[11px]">
                                {hasSeeds ? `[${example?.seeds?.join(", ")}]` : "not recorded"}
                              </dd>
                            </div>
                          </dl>
                          <p className="text-[10px] text-muted-foreground" data-run-award-note>
                            {award.known && award.certified
                              ? retainedCount === null
                                ? "Certified award: a pre-verdict entitlement, not an inventory receipt — the chest count above stays unverified."
                                : "Certified award: a pre-verdict entitlement, not an inventory receipt."
                              : award.known
                                ? "Zero from the native win/loss gate, not an inventory receipt."
                                : "Award unknown on this run; callbacks are observed diagnostics, not chests."}
                          </p>
                          <div className="mt-auto flex items-center gap-2">
                            <Button
                              type="button"
                              size="sm"
                              onClick={() => watchRun(label)}
                              disabled={
                                !api ||
                                replayBlocked ||
                                !hasSeeds ||
                                stateLabel === "Running" ||
                                stateLabel === "Saving"
                              }
                              data-action="watch-run"
                            >
                              <Play className="mr-1 h-3.5 w-3.5" /> Watch this run
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant={active ? "secondary" : "ghost"}
                              onClick={() => setExampleLabel(label)}
                              data-action="inspect-run"
                            >
                              {active ? "Selected" : "Select"}
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>
            </section>

            <Card data-optimizer-thresholds data-selected-candidate={selected?.id ?? ""}>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Thresholds &amp; ranges</CardTitle>
                <CardDescription>
                  The host's own measured probes for the selected strategy: the rung that was probed,
                  the effective stat the fight actually used, what each rung produced and whether it
                  stayed viable. Nothing here is smoothed, extended or inferred — a bound the host did
                  not bracket stays unbracketed.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {status?.error ? (
                  <Alert data-probe-refusal>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>Host refusal</AlertTitle>
                    <AlertDescription>
                      <p>{status.error}</p>
                      <p className="text-[11px] text-muted-foreground">
                        The host refuses an illegal ladder or an unestablished axis with its own
                        reason, which is what is quoted above.
                      </p>
                    </AlertDescription>
                  </Alert>
                ) : null}

                {!selected ? (
                  <p className="text-xs text-muted-foreground" data-probe-empty>
                    Select a strategy to read its measured thresholds and to start a probe.
                  </p>
                ) : (
                  <>
                    {encounterAwareActive ? (
                      <p className="text-[11px] text-muted-foreground" data-probe-encounter-block>
                        Range probes are replaced by the encounter-aware boundary questions above while
                        that mode is active; the measured probes below stay readable as history.
                      </p>
                    ) : (
                      <>
                        <ProbeAxisControl
                          disabled={!api || busy !== null}
                          starting={busy?.startsWith("Probe ") ?? false}
                          axis={probeAxis}
                          axis2={probeAxis2}
                          allowUnestablished={allowUnestablished}
                          onAxis={setProbeAxis}
                          onAxis2={setProbeAxis2}
                          onAllowUnestablished={handleAllowUnestablished}
                          onStart={() => void startProbe()}
                        />
                        <p className="text-[10px] text-muted-foreground">
                          The host picks the ladder and its rungs. An illegal ladder or an unestablished
                          axis is refused with the host's own reason, shown above.
                        </p>
                      </>
                    )}

                    {selectedProbes.length ? (
                      <div className="space-y-3">
                        {selectedProbes.map((probe, index) => (
                          <ProbeAnalysis
                            key={`${probe.pivotId}-${probe.axis}-${probe.axis2 ?? ""}-${index}`}
                            probe={probe}
                            lastProbe={lastProbe}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="space-y-2" data-probe-none>
                        <p className="text-xs text-muted-foreground">
                          No threshold probe has been measured for {selected.label} yet. Choose an axis
                          above and start one; when the host finishes, the measured ladder appears here.
                        </p>
                        {otherProbes.length ? (
                          <p className="text-xs text-muted-foreground" data-probe-other-strategy>
                            Probes do exist for{" "}
                            {otherProbes
                              .map((probe) => `${probe.pivotLabel ?? probe.pivotId} (${shortId(probe.pivotId)})`)
                              .join(", ")}{" "}
                            — a different strategy, not the selected one, so they are not shown here.
                          </p>
                        ) : null}
                      </div>
                    )}

                    {lastProbe && lastProbe.pivotId === selected.id && lastProbe.refused?.length ? (
                      <div className="space-y-1" data-probe-refused>
                        <div className="text-[11px] font-medium text-foreground/90">
                          Rungs the host refused on the most recent probe
                        </div>
                        <ul className="space-y-1 text-[11px] text-muted-foreground">
                          {lastProbe.refused.map((entry, index) => (
                            <li key={`${entry.value ?? "?"}-${index}`} className="tabular-nums">
                              {probeValue(entry.value)}
                              {entry.value2 === null || entry.value2 === undefined
                                ? ""
                                : ` × ${probeValue(entry.value2)}`}
                              {" — "}
                              {entry.reason ?? "the host gave no reason"}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </>
                )}
              </CardContent>
            </Card>

            <Card data-optimizer-controls>
              <CardHeader>
                <CardTitle className="text-base">Search settings &amp; library</CardTitle>
                <CardDescription>
                  Bound the search's duty cycle and manage this library's own files. The worker count,
                  Start, Pause, Stop and New library stay pinned in the toolbar at the top of the
                  page; Pause or Stop finishes and saves the current batch before releasing the
                  workers.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {status && !status.compatible ? (
                  <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
                    The combat engine changed since this library was written, so starting stays blocked
                    until a new library is opened. Your existing runs stay readable and are never
                    overwritten.
                  </p>
                ) : null}
                <div className="flex flex-wrap items-end gap-6">
                  <div className="w-64 space-y-1">
                    <Label className="text-xs">Duty cycle: {duty.toFixed(2)}</Label>
                    <Slider
                      value={[duty]}
                      min={0.1}
                      max={1}
                      step={0.05}
                      onValueChange={(value) => setDuty(value[0] ?? DEFAULT_DUTY)}
                      disabled={!api}
                      data-optimizer-duty
                    />
                    <p className="text-[10px] text-muted-foreground">
                      0.10–1.00 of wall-clock busy; lower is gentler. Each worker stays capped at
                      768 MiB and runs the same canonical engine.
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void importBuild()}
                    disabled={!api || busy !== null}
                    data-action="optimizer-import"
                  >
                    <Upload className="mr-1 h-3.5 w-3.5" /> Import supplied build…
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void exportBuild()}
                    disabled={!api || busy !== null || !selected}
                    data-action="optimizer-export"
                  >
                    <Download className="mr-1 h-3.5 w-3.5" /> Export chosen build…
                  </Button>
                  <Button type="button" variant="outline" disabled={!api || busy !== null || stateLabel === "Running" || stateLabel === "Saving"}
                    onClick={() => void sendCommand("All 20 encounters", "all_encounters", {})}>
                    Search all 20 encounters
                  </Button>
                </div>

                {busy ? (
                  <p className="text-xs text-muted-foreground" data-optimizer-busy>
                    {busy}…
                  </p>
                ) : null}
                <p className="text-[10px] text-muted-foreground">
                  Import and export use a file picker; New library is in the toolbar above and is
                  available only while search is paused. Each worker is limited to 768 MiB; one
                  simulation can run for at most 180 seconds.
                </p>
              </CardContent>
            </Card>

              </div>
            </details>

            <details className="rounded-lg border p-4" data-optimizer-technical>
              <summary className="cursor-pointer select-none text-sm font-semibold">
                Technical details — diagnostics, reliability, families, archive
              </summary>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Diagnostics only. Prize callbacks here are observed combat activity and are not retained
                chests. Reliability, seeds and family cells stay provisional.
              </p>
              <p className="mt-1 text-[11px] text-muted-foreground tabular-nums" data-optimizer-live>
                Since first Start {formatDuration(status?.sessionElapsedSeconds)} (active time) · average{" "}
                {status?.averageSimulationSeconds == null
                  ? "measuring…"
                  : `${status.averageSimulationSeconds.toFixed(2)} s`}{" "}
                per run ({formatInt(status?.timedRuns)} timed) · current batch{" "}
                {status?.currentRunElapsedSeconds == null
                  ? "idle"
                  : `${status.currentRunElapsedSeconds.toFixed(1)} s`}
              </p>
              <div className="mt-4 space-y-6">
            <Card data-optimizer-ranked-candidates>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  {anyRetained
                    ? "Raw candidate list — ranked by chest yield"
                    : anyChests ? "Raw candidate list — ranked by chests per run" : "Raw candidate list — ranked by win reliability"}
                </CardTitle>
                <CardDescription>
                  Every candidate the runner emitted, unfiltered by family: one row per mutation, so twenty
                  near-identical ideas appear here as twenty rows. The Strategy families card above is the view
                  that collapses them.
                  {anyRetained
                    ? " Ranked by mean retained chests per run, with the win 95% lower bound as the tie-break."
                    : anyChests
                      ? " Ranked by mean chests per resolved run, tie-broken by the win 95% lower bound and then by how many runs the mean covers. A mean over a handful of runs is labelled, not trusted. Chests from different fights are not equivalent, so compare within one encounter."
                      : " No resolved run yet, so the ranking falls back to the win 95% lower bound, then sample size."}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {visibleCandidates.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No candidate is visible for the current fight filter.
                  </p>
                ) : null}
                {visibleCandidates.map((candidate, index) => {
                  const reading = retainedReading(candidate.selection);
                  const chests = candidateChests(candidate);
                  const win = candidateWin(candidate);
                  const active = candidate.id === selectedId;
                  const encounterId = encounterIdOf(candidate);
                  const encounter = encounterId === null ? undefined : encounters?.[String(encounterId)];
                  return (
                    <div
                      key={candidate.id}
                      className={cn(
                        "flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2",
                        active && "border-primary/50 bg-primary/5",
                      )}
                      data-best-build={candidate.id}
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="font-mono text-xs tabular-nums text-muted-foreground">
                          #{index + 1}
                        </span>
                        <div className="min-w-40">
                          <div className="text-sm font-medium">{candidate.label}</div>
                          <div className="text-[10px]" data-best-build-encounter>
                            <span className="font-medium text-foreground/80">
                              {encounterLabel(encounterId, encounter)}
                            </span>
                            <span className="text-muted-foreground">
                              {" · "}
                              {familyDetail(candidate.family)} · {candidate.source}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
                        <span className="text-muted-foreground">
                          {reading.known ? "retained / run" : "chests / run"}
                        </span>
                        <span
                          className={cn(
                            "font-mono text-lg font-semibold tabular-nums",
                            reading.known || chests ? "text-foreground" : "text-muted-foreground",
                          )}
                          data-best-build-retained
                        >
                          {reading.known
                            ? formatNumber(reading.mean, 2)
                            : chests
                              ? formatNumber(chests.mean, 2)
                              : formatRetainedPerRun(reading)}
                        </span>
                        {chests && !reading.known ? (
                          <span className="text-[10px] text-muted-foreground">
                            over {formatInt(chests.samples)} {SCOPE_LABEL[chests.scope]}
                          </span>
                        ) : null}
                        <span className="tabular-nums text-muted-foreground" data-best-build-win>
                          win {formatPercent(win?.rate)} · LB {formatNumber(win?.lower, 2)}
                        </span>
                        <span className="text-muted-foreground">{strategyReliability(candidate)}</span>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant={active ? "default" : "outline"}
                        onClick={() => setSelectedId(candidate.id)}
                        data-action="select-best-build"
                      >
                        {active ? "Selected" : "Inspect"}
                      </Button>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
            <Card data-optimizer-status>
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <CardTitle className="text-base">Library status</CardTitle>
                    <CardDescription>
                      Polled every {POLL_INTERVAL_MS / 1000} seconds; the counts are the runner's own.
                    </CardDescription>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void refresh()}
                    disabled={!api}
                    data-action="optimizer-refresh"
                  >
                    <RefreshCw className="mr-1 h-3.5 w-3.5" /> Refresh
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  <StatTile label="State" value={stateLabel} />
                  <StatTile label="Total runs" value={formatInt(status?.totalRuns)} />
                  <StatTile label="Proposals" value={formatInt(status?.proposals)} hint="legal mutations emitted" />
                  <StatTile label="Improvements" value={formatInt(status?.improvements)} hint="archive replacements" />
                  <StatTile label="Latest improvement" value={formatInt(status?.lastImprovementRun)} hint="run index" />
                  <StatTile label="Library on disk" value={formatBytes(status?.diskBytes)} />
                </div>
                <p className="text-[11px] text-muted-foreground" data-optimizer-acceleration>
                  {status?.acceleration?.enabled
                    ? `Bulk engine: canonical engine + ${(status.acceleration.modules ?? []).length} parity-checked accelerator(s) — exact loop/setup acceleration, identical digests.`
                    : "Bulk engine: canonical engine. Exact accelerators stay off until a recorded parity report matches this engine snapshot."}
                </p>
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                    <StatTile label="Since first Start" value={`${Math.floor((status?.sessionElapsedSeconds ?? 0) / 60)}m ${Math.floor((status?.sessionElapsedSeconds ?? 0) % 60)}s`} hint="active Running and Saving time" />
                    <StatTile label="Average simulation" value={status?.averageSimulationSeconds == null ? "Measuring…" : `${status.averageSimulationSeconds.toFixed(2)} s`} hint={`${status?.timedRuns ?? 0} timed runs; excludes deliberate idle time`} />
                    <StatTile label="Current batch elapsed" value={status?.currentRunElapsedSeconds == null ? "Idle" : `${status.currentRunElapsedSeconds.toFixed(1)} s`} />
                    <StatTile label="Estimated runs / hour" value={status?.averageSimulationSeconds ? formatInt(3600 * workers * duty / status.averageSimulationSeconds) : "—"} hint="at selected worker count and duty cycle; estimate" />
                  </div>
                  <p className="text-xs text-muted-foreground">Search screens new builds before spending 64 validation seeds on them. Two initial losses defer a build; they do not prove it can never work. Each encounter has its own strategy families.</p>
                  <ProgressRow
                    label={`Selection-complete builds (${completeSelection}/${candidates.length})`}
                    value={candidates.length ? (completeSelection / candidates.length) * 100 : 0}
                  />
                  {selected ? (
                    <>
                      <ProgressRow
                        label={`Discovery runs for ${selected.label} (${formatInt(selected.discovery?.n)}/${DISCOVERY_RUNS})`}
                        value={selected.discovery ? (selected.discovery.n / DISCOVERY_RUNS) * 100 : 0}
                      />
                      <ProgressRow
                        label={`Validation runs for ${selected.label} (${formatInt(selected.validation?.n)}/${SELECTION_RUNS})`}
                        value={selected.validation ? (selected.validation.n / SELECTION_RUNS) * 100 : 0}
                      />
                    </>
                  ) : null}
                </div>
              </CardContent>
            </Card>

            <Card data-optimizer-families>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Layers className="h-4 w-4" /> Families (provisional)
                </CardTitle>
                <CardDescription>
                  Coarse measured behavioural cells from the runner's own validation runs — a hypothesis about
                  a family, not one family per parameter vector. Candidates stay provisional until a family's
                  selection bank is complete.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {candidates.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No candidates in this library yet.</p>
                ) : null}
                {families.map(([family, list]) => (
                  <div key={family} className="space-y-2 rounded-md border p-3" data-optimizer-family={family}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold">{family}</span>
                      <Badge variant="outline" className="text-[10px]">
                        {list.length} candidate{list.length === 1 ? "" : "s"}
                      </Badge>
                      {list.every((candidate) => !isCompleteSelection(candidate)) ? (
                        <ToneBadge category="warning">provisional</ToneBadge>
                      ) : null}
                    </div>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Build</TableHead>
                          <TableHead>Source</TableHead>
                          <TableHead>Selection sample</TableHead>
                          <TableHead>Win rate (95% CI)</TableHead>
                          <TableHead>Callbacks (diagnostic)</TableHead>
                          <TableHead />
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {list.map((candidate) => (
                          <TableRow key={candidate.id} data-candidate={candidate.id}>
                            <TableCell>
                              <div className="font-medium">{candidate.label}</div>
                              <div className="font-mono text-[10px] text-muted-foreground">
                                {shortId(candidate.id)}
                              </div>
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline" className="text-[10px]">
                                {candidate.source}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">
                              {formatInt(candidate.selection?.n)}/{SELECTION_RUNS} ·{" "}
                              {candidate.selection?.comparable ? "comparable" : "partial"}
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">
                              {formatPercent(candidate.selection?.winRate)} · [
                              {formatInterval(candidate.selection?.winInterval)}]
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">
                              {formatNumber(candidate.selection?.callbackMean, 2)}
                            </TableCell>
                            <TableCell>
                              <Button
                                type="button"
                                size="sm"
                                variant={candidate.id === selectedId ? "default" : "outline"}
                                onClick={() => setSelectedId(candidate.id)}
                                data-action="select-candidate"
                              >
                                {candidate.id === selectedId ? "Selected" : "Select"}
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                ))}
              </CardContent>
            </Card>

            {selected ? (
              <Card data-optimizer-selection data-selected-candidate={selected.id}>
                <CardHeader>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">{selected.label}</CardTitle>
                      <CardDescription>
                        <span className="font-mono">{selected.id}</span> · {selected.source} · family{" "}
                        {selected.family ?? NO_FAMILY} · {formatInt(selected.validationRuns)} validation run(s)
                        recorded
                      </CardDescription>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => void exportBuild()}
                        disabled={!api || busy !== null}
                        data-action="optimizer-export-selected"
                      >
                        <Download className="mr-1 h-3.5 w-3.5" /> Export…
                      </Button>
                      <Button type="button" size="sm" variant="outline"
                        disabled={!api || busy !== null || selected.source === "saved"}
                        onClick={() => void sendCommand("Keep build", "keep", { id: selected.id })}>
                        <FilePlus2 className="mr-1 h-3.5 w-3.5" />
                        {selected.source === "saved" ? "Kept in library" : "Keep build"}
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Distributions &amp; reliability
                    </p>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Bank</TableHead>
                          <TableHead>n</TableHead>
                          <TableHead>wins</TableHead>
                          <TableHead>losses</TableHead>
                          <TableHead>censored</TableHead>
                          <TableHead>win rate</TableHead>
                          <TableHead>win 95% CI</TableHead>
                          <TableHead>failure</TableHead>
                          <TableHead>no verdict by limit</TableHead>
                          <TableHead>callback mean</TableHead>
                          <TableHead>SD</TableHead>
                          <TableHead>min</TableHead>
                          <TableHead>p10</TableHead>
                          <TableHead>max</TableHead>
                          <TableHead>resources</TableHead>
                          <TableHead>survivors</TableHead>
                          <TableHead>ticks</TableHead>
                          <TableHead>comparable</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(
                          [
                            ["discovery", selected.discovery],
                            ["validation", selected.validation],
                            [`selection (first ${SELECTION_RUNS})`, selected.selection],
                          ] as Array<[string, Summary]>
                        ).map(([label, summary]) => (
                          <TableRow key={label} data-bank={label}>
                            <TableCell className="text-xs font-medium">{label}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatInt(summary?.n)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatInt(summary?.wins)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatInt(summary?.losses)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatInt(summary?.censored)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatPercent(summary?.winRate)}</TableCell>
                            <TableCell className="text-xs tabular-nums">
                              [{formatInterval(summary?.winInterval)}]
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">{formatPercent(summary?.failureRate)}</TableCell>
                            <TableCell className="text-xs tabular-nums">
                              {formatPercent(summary?.unresolvedRate)}
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">
                              {formatNumber(summary?.callbackMean, 3)}
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(summary?.callbackSD, 3)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(summary?.callbackMin, 3)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(summary?.callbackP10, 3)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(summary?.callbackMax, 3)}</TableCell>
                            <TableCell className="text-xs tabular-nums">
                              {formatNumber(summary?.meanResources, 3)}
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">
                              {formatNumber(summary?.meanSurvivors, 3)}
                            </TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(summary?.meanTicks, 1)}</TableCell>
                            <TableCell className="text-xs">{summary?.comparable ? "yes" : "no"}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      Validation statistics cover all completed samples. The selection bank stays fixed;
                      replay examples retain the first 64 and latest 192 validation seeds. These sampling
                      intervals do not certify real-game reliability. Prize-callback columns are observed
                      diagnostics of combat activity, not retained chests.
                    </p>
                    {selected.validation.callbackHistogram && (
                      <div className="mt-3 flex flex-wrap gap-2" aria-label="Callback distribution over resolved validation runs">
                        {Object.entries(selected.validation.callbackHistogram).map(([range, count]) => (
                          <Badge key={range} variant="outline">{range} callbacks: {count} runs</Badge>
                        ))}
                      </div>
                    )}
                  </div>

                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Stored exact runs (seeds, digest, outcome)
                    </p>
                    {exampleLabels.length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        No stored runs yet, so there is no seed pair to replay.
                      </p>
                    ) : (
                      <Table data-optimizer-examples>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Run</TableHead>
                            <TableHead>Outcome</TableHead>
                            <TableHead>Award · retained</TableHead>
                            <TableHead>Seeds [math, lib]</TableHead>
                            <TableHead>Run digest</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {exampleLabels.map((label) => {
                            const example = selected.examples?.[label];
                            const outcome = runOutcome(example);
                            const award = awardReading(example);
                            const retainedCount = retainedRunValue(example);
                            return (
                              <TableRow key={label} data-example={label} data-run-outcome={outcome}>
                                <TableCell className="text-xs">{label}</TableCell>
                                <TableCell className="text-xs">
                                  <ToneBadge category={outcomeCategory(outcome)}>
                                    {RUN_OUTCOME_LABEL[outcome]}
                                  </ToneBadge>
                                </TableCell>
                                <TableCell className="text-[11px]" data-run-award-cell>
                                  <span className="font-mono text-foreground">{awardLabel(award)}</span>
                                  <span className="ml-2 text-muted-foreground" data-run-retained>
                                    {retainedCount === null ? "retained unverified" : `retained ${formatInt(retainedCount)}`}
                                  </span>
                                </TableCell>
                                <TableCell className="font-mono text-[11px]">
                                  {Array.isArray(example?.seeds) ? `[${example.seeds.join(", ")}]` : "—"}
                                </TableCell>
                                <TableCell className="font-mono text-[10px] text-muted-foreground">
                                  {example?.digest ? example.digest.slice(0, 16) : "—"}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    )}
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      Watching a run always uses the runner's exact scenario and that stored seed pair; only
                      the selected trace is generated. Prize callbacks are not retained chests.
                    </p>
                  </div>

                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Effective stats of this build (runner-prepared values)
                    </p>
                    {Object.keys(selected.stats ?? {}).length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        The runner reported no prepared stats for this build.
                      </p>
                    ) : (
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Fighter</TableHead>
                            <TableHead>Kind</TableHead>
                            <TableHead>Weapon</TableHead>
                            <TableHead>Range</TableHead>
                            <TableHead>Eff. defense</TableHead>
                            <TableHead>Avg training</TableHead>
                            <TableHead>Parameters (value/max)</TableHead>
                            <TableHead>Skill MP costs</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {Object.entries(selected.stats).map(([fighter, stat]) => {
                            const parameters = Object.entries(stat.parameters ?? {});
                            const skillCosts = stat.skillCosts ?? [];
                            return (
                              <TableRow key={fighter} data-fighter={fighter}>
                                <TableCell className="text-xs font-medium">{fighter}</TableCell>
                                <TableCell className="text-xs">{stat.monster ? "monster" : "hero"}</TableCell>
                                <TableCell className="text-xs">{stat.weaponId ?? "—"}</TableCell>
                                <TableCell className="text-xs tabular-nums">
                                  {formatNumber(stat.weaponRange, 0)}
                                </TableCell>
                                <TableCell className="text-xs tabular-nums">
                                  {formatNumber(stat.effectiveDefense, 2)}
                                </TableCell>
                                <TableCell className="text-xs tabular-nums">
                                  {formatNumber(stat.averageTrainingLevel, 2)}
                                </TableCell>
                                <TableCell className="font-mono text-[11px]">
                                  {parameters.length === 0
                                    ? "—"
                                    : parameters
                                        .map(
                                          ([pid, param]) =>
                                            `param ${pid}: ${formatNumber(param?.value, 2)}/${formatNumber(param?.maximum, 0)}`,
                                        )
                                        .join(" · ")}
                                </TableCell>
                                <TableCell className="font-mono text-[11px]">
                                  {skillCosts.length === 0
                                    ? "—"
                                    : skillCosts.map((row) => `skill ${row.skillId}: ${row.cost}`).join(" · ")}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    )}
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      Parameter ids are the runner's own numeric keys; no stat names are invented. These are
                      supplied or canonically mutated builds, not an achievability claim.
                    </p>
                  </div>

                  <details className="text-[11px]">
                    <summary className="cursor-pointer text-muted-foreground">
                      Scenario that produced this build
                    </summary>
                    <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted/40 p-2">
                      {JSON.stringify(selected.scenario, null, 2)}
                    </pre>
                  </details>
                </CardContent>
              </Card>
            ) : null}

            {selected ? (
              <Card data-optimizer-region data-region-family={selected.family ?? NO_FAMILY}>
                <CardHeader>
                  <CardTitle className="text-base">
                    Recommended / viable joint stat region — {selected.family ?? NO_FAMILY}
                  </CardTitle>
                  <CardDescription>
                    Only builds in this same measured family with a complete, comparable {SELECTION_RUNS}-run
                    selection bank are shown. Recommended = win-interval lower bound ≥{" "}
                    {RECOMMENDED_WIN_LOW.toFixed(2)} and callback mean within{" "}
                    {Math.round(RECOMMENDED_CALLBACK_SHARE * 100)}% of the best observed in the family. Viable
                    = every such tested build.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {region.columns.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      No build in this family has a complete {SELECTION_RUNS}-run selection bank with a lower
                      win-interval bound ≥ {RECOMMENDED_WIN_LOW.toFixed(2)} yet.
                    </p>
                  ) : (
                    <>
                      <p className="text-xs text-muted-foreground" data-optimizer-best-observed>
                        Best observed callback mean in this family:{" "}
                        <span className="font-mono text-foreground">{formatNumber(region.bestObserved, 3)}</span>.
                        Best observed is the best of these tested builds only — it is never a global optimum.
                      </p>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="min-w-48">Stat (fighter · key)</TableHead>
                            {region.columns.map(({ candidate, recommended }) => (
                              <TableHead key={candidate.id} className="text-right">
                                <div className="font-mono text-[10px]">{shortId(candidate.id)}</div>
                                <div className="text-[9px] font-normal text-muted-foreground">
                                  {recommended ? "recommended" : "viable"}
                                </div>
                              </TableHead>
                            ))}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {region.rows.map((row) => (
                            <TableRow key={row.key}>
                              <TableCell className="text-[11px]">{row.label}</TableCell>
                              {row.values.map((value, index) => (
                                <TableCell
                                  key={region.columns[index].candidate.id}
                                  className="text-right font-mono text-[11px] tabular-nums"
                                >
                                  {formatNumber(value, 2)}
                                </TableCell>
                              ))}
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                      <p className="text-[10px] text-muted-foreground">
                        Each column is one tested joint build and the cells are its measured values — not
                        independent per-stat ranges or min/max promises. No interpolation and no in-game
                        achievability certification are implied.
                      </p>
                    </>
                  )}
                </CardContent>
              </Card>
            ) : null}

            <Card data-optimizer-archive>
              <CardHeader>
                <CardTitle className="text-base">Archive</CardTitle>
                <CardDescription>
                  One entry per measured family cell. Membership is provisional; the quality triple is the
                  runner's own (callback mean, win-interval lower bound, −mean resources).
                </CardDescription>
              </CardHeader>
              <CardContent>
                {archive.length === 0 ? (
                  <p className="text-xs text-muted-foreground">The archive is empty.</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Cell</TableHead>
                        <TableHead>Build</TableHead>
                        <TableHead>Callbacks (mean)</TableHead>
                        <TableHead>Win 95% lower</TableHead>
                        <TableHead>−resources</TableHead>
                        <TableHead />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {archive.map((row) => {
                        const quality = parseQuality(row.quality);
                        const known = candidates.some((candidate) => candidate.id === row.candidate);
                        return (
                          <TableRow key={row.cell} data-archive-cell={row.cell}>
                            <TableCell className="text-[11px]">{row.cell}</TableCell>
                            <TableCell className="font-mono text-[10px]">{shortId(row.candidate)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(quality?.[0], 3)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(quality?.[1], 3)}</TableCell>
                            <TableCell className="text-xs tabular-nums">{formatNumber(quality?.[2], 3)}</TableCell>
                            <TableCell>
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => setSelectedId(row.candidate)}
                                disabled={!known}
                                data-action="select-archive-candidate"
                              >
                                Select
                              </Button>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>

            <Card data-optimizer-provenance>
              <CardHeader>
                <CardTitle className="text-base">Provenance</CardTitle>
                <CardDescription>
                  Digests of the canonical runner sources and the live runtime data this library is bound to.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Digest</TableHead>
                      <TableHead>Mode</TableHead>
                      <TableHead>Files</TableHead>
                      <TableHead>Missing</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow>
                      <TableCell className="break-all font-mono text-[10px]">
                        {provenance?.digest ?? "—"}
                      </TableCell>
                      <TableCell className="text-xs">{provenance?.mode ?? "—"}</TableCell>
                      <TableCell className="text-xs tabular-nums">{formatInt(provenance?.count)}</TableCell>
                      <TableCell className="text-xs">
                        {provenance?.missing?.length ? provenance.missing.join(", ") : "none"}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
                {provenance?.files ? (
                  <details className="text-[11px]">
                    <summary className="cursor-pointer text-muted-foreground">
                      File digests ({Object.keys(provenance.files).length})
                    </summary>
                    <Table className="mt-1">
                      <TableHeader>
                        <TableRow>
                          <TableHead>File</TableHead>
                          <TableHead>sha256</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {Object.entries(provenance.files).map(([file, digest]) => (
                          <TableRow key={file}>
                            <TableCell className="text-[11px]">{file}</TableCell>
                            <TableCell className="break-all font-mono text-[10px]">{digest ?? "missing"}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </details>
                ) : null}
              </CardContent>
            </Card>
              </div>
            </details>
          </>
        )}
      </div>
    </div>
  );
}
