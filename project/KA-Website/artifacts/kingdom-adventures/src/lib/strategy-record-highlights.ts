/**
 * The Encounter overview's "newly improved record" bookkeeping.
 *
 * The overview shows four measured readings per fight (highest potential, highest earned, the
 * loss-only mean potential and the mean earned over resolved runs). When one of those readings
 * strictly increases after the open library's own first data has landed, its cell glows green for
 * {@link RECORD_HIGHLIGHT_MS}. The rules are deliberately narrow and live here, away from React, so
 * they can be driven with an explicit clock:
 *
 *   * only the four *readings* are compared - a sample count (`n`) is not a reading, so a run that
 *     only grows a mean's sample size never lights a cell;
 *   * a reading that was unknown becomes known counts as new, because the host has just measured
 *     something it had nothing for before;
 *   * an unchanged, lower or still-unknown reading does not light;
 *   * every encounter/difficulty/metric combination is its own key, so two cells improve and expire
 *     independently, and a later improvement on the same cell replaces its earlier deadline;
 *   * the first observation of a library is only a baseline, and switching libraries re-baselines
 *     and clears every glow, so a history that was already there never lights.
 *
 * The page owns the timers and the React state; this module owns the comparison, the expiry
 * arithmetic and the baseline/library state machine.
 */

/** How long one metric cell keeps its green "just improved" glow. */
export const RECORD_HIGHLIGHT_MS = 60_000;

/** The four readings the overview displays for one encounter/difficulty. */
export const ENCOUNTER_METRIC_NAMES = [
  "highest-potential",
  "highest-earned",
  "avg-potential",
  "avg-earned",
] as const;

export type EncounterMetricName = (typeof ENCOUNTER_METRIC_NAMES)[number];

/** A cell's identity: encounter, difficulty (`defeatCount`) and metric, so no two cells share one. */
export function recordCellKey(encounterId: number, defeatCount: number, metric: string): string {
  return `${encounterId}:${defeatCount}:${metric}`;
}

/**
 * One observation of the overview's readings, keyed by {@link recordCellKey}. A missing key, `null`
 * and a non-finite number all mean the host has not measured that cell.
 */
export type RecordReadings = Record<string, number | null | undefined>;

/** A reading is a finite number or unknown; a null is never read as a zero. */
function reading(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Which cells strictly increased between two observations. An unknown reading that becomes known
 * counts as new; an unchanged or lower reading does not. Keys absent from `current` are ignored and
 * keys absent from `previous` are treated as previously unknown, never as a zero.
 */
export function improvedRecordCells(previous: RecordReadings, current: RecordReadings): string[] {
  const improved: string[] = [];
  for (const [key, raw] of Object.entries(current)) {
    const next = reading(raw);
    if (next === null) continue;
    const prior = reading(previous[key]);
    if (prior === null || next > prior) improved.push(key);
  }
  return improved;
}

/** One cell's glow deadline: cell key -> the epoch-ms instant the glow ends. */
export type RecordHighlightState = Record<string, number>;

export type RecordHighlightStep = {
  /** The glow deadlines that are still live at `now`, plus any just started. */
  state: RecordHighlightState;
  /** The cells that strictly improved in this step and should (re)arm their timer. */
  started: string[];
};

/**
 * Drop every glow whose deadline has already passed at `now`. Returns the same object when nothing
 * expired, so a caller that stores it in state can bail out of a re-render.
 */
export function pruneRecordHighlights(state: RecordHighlightState, now: number): RecordHighlightState {
  let changed = false;
  const next: RecordHighlightState = {};
  for (const [key, expiry] of Object.entries(state)) {
    if (expiry > now) next[key] = expiry;
    else changed = true;
  }
  return changed ? next : state;
}

/**
 * Advance one observation: expire anything already due, then start a fresh glow on every cell that
 * strictly improved. A repeat improvement on the same cell replaces its deadline, so the window runs
 * from the latest improvement; a cell that had already expired and improves again lights again.
 */
export function advanceRecordHighlights(
  state: RecordHighlightState,
  previous: RecordReadings,
  current: RecordReadings,
  now: number,
  durationMs: number = RECORD_HIGHLIGHT_MS,
): RecordHighlightStep {
  const live = pruneRecordHighlights(state, now);
  const started = improvedRecordCells(previous, current);
  if (started.length === 0) return { state: live, started };
  const next: RecordHighlightState = { ...live };
  for (const key of started) next[key] = now + durationMs;
  return { state: next, started };
}

/** The whole state the caller must carry between observations. */
export type RecordHighlightTracker = {
  /** The library the tracker is baselined against; a different library means "start over". */
  library: string | null | undefined;
  /** The last observation, or null before this library's first data has landed. */
  previous: RecordReadings | null;
  /** The live glow deadlines. */
  state: RecordHighlightState;
};

export function createRecordHighlightTracker(
  library: string | null | undefined = undefined,
): RecordHighlightTracker {
  return { library, previous: null, state: {} };
}

export type RecordHighlightTracking = {
  tracker: RecordHighlightTracker;
  /** The cells that strictly improved and should (re)arm their timer. */
  started: string[];
  /** True when the library changed: clear every timer and every glow before using the tracker. */
  reset: boolean;
};

/**
 * Fold one observation into the tracker.
 *
 * A different library resets everything (a fresh, empty baseline and no glows), and the first
 * observation of a library only records the baseline, so neither the library that was open before
 * nor a record that was already in the new library can light. Nothing is recorded while `ready` is
 * false, because the library's own first data has not landed yet and a partial reading would become
 * a false baseline.
 */
export function trackRecordHighlights(
  tracker: RecordHighlightTracker,
  library: string | null | undefined,
  readings: RecordReadings,
  ready: boolean,
  now: number,
  durationMs: number = RECORD_HIGHLIGHT_MS,
): RecordHighlightTracking {
  if (tracker.library !== library) {
    return { tracker: createRecordHighlightTracker(library), started: [], reset: true };
  }
  if (!ready) return { tracker, started: [], reset: false };
  if (tracker.previous === null) {
    return { tracker: { ...tracker, previous: { ...readings }, state: {} }, started: [], reset: false };
  }
  const step = advanceRecordHighlights(tracker.state, tracker.previous, readings, now, durationMs);
  return {
    tracker: { library, previous: { ...readings }, state: step.state },
    started: step.started,
    reset: false,
  };
}
