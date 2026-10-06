/**
 * Pure helpers for the Strategy-guide LINKED STAT DIALS.
 *
 * These functions run entirely on the already-loaded player-region summary. They never fetch, never
 * run a battle, never send a command and never interpolate. Matching is EXACT on the measured stops
 * actually published by the loaded points, and the "minimum" of an axis is only the lowest stop seen
 * in the CURRENT evidence - never a proven global floor and never an independent guarantee.
 *
 * An unmeasured combination (a typed value no point published, or a set of pinned values no single
 * point carries together) resolves to an explicit unknown plus the closest MEASURED alternatives and
 * the exact stat differences a player would have to change. Nothing here creates a continuous safe
 * box: the only admissible values are point measurements, so axes without evidence stay unknown.
 *
 * Scoping: analysis is per archetype (its own points only). Identical stat values in another
 * archetype, region or policy group are never pooled.
 */
import type {
  PlayerRegionArchetype,
  PlayerRegionPoint,
  PlayerRegionStatVector,
} from "@/types/player-regions";

/** One flattened measured point, tagged with its archetype and region. */
export type StatPoint = {
  archetypeId: string;
  regionId: string | null;
  candidateId: string | null;
  label: string | null;
  stats: PlayerRegionStatVector;
  meanEarned: number | null;
  partialMean: number | null;
  samples: number | null;
  total: number | null;
  unknownCount: number | null;
  uncertainty: unknown;
  evidenceStatus: string | null;
  classification: string | null;
  point: PlayerRegionPoint;
};

/**
 * One observed stat axis inside an archetype: every distinct measured stop the loaded points publish
 * for that `<role>.<stat>` field, ascending. `min`/`max` are the lowest/highest stop OBSERVED here.
 */
export type StatAxis = {
  field: string;
  role: string;
  stat: string;
  stops: number[];
  min: number;
  max: number;
};

/** A pinned axis value chosen by the reader (a measured stop, or an arbitrary typed number). */
export type StatPin = { field: string; value: number };

/** The difference between one measured point and one pinned value on a single field. */
export type StatPinDelta = {
  field: string;
  pinned: number;
  /** The point's measured value on the field, or null when the point publishes none (unknown). */
  measured: number | null;
  /** measured - pinned, or null when the point publishes no value (nothing to subtract). */
  delta: number | null;
};

/**
 * One measured point considered against the current pins. For a MATCH every pinned field is equal,
 * and `otherStats` are the measured values the remaining observed axes would need to take for this
 * reward. For a nearest alternative `deltas` carry the exact per-field change required.
 */
export type StatCombination = {
  archetypeId: string;
  regionId: string | null;
  candidateId: string | null;
  label: string | null;
  meanEarned: number | null;
  partialMean: number | null;
  samples: number | null;
  total: number | null;
  unknownCount: number | null;
  uncertainty: unknown;
  evidenceStatus: string | null;
  classification: string | null;
  /** The pinned fields this point publishes, with the value it actually carries. */
  pinnedStats: { field: string; value: number }[];
  /** Measured values of every OTHER observed axis on this point (the compensation). */
  otherStats: { field: string; value: number }[];
  /** Pinned fields this point publishes no value for (explicit unknown). */
  missingFields: string[];
  deltas: StatPinDelta[];
  /** Sum of |measured - pinned| over the pins this point publishes; lower is closer. */
  distance: number;
};

export type StatPinResolution = {
  state: "free" | "matched" | "unknown";
  pins: StatPin[];
  axes: StatAxis[];
  /** Measured points matching every pin exactly (non-empty only when `state === "matched"`). */
  combinations: StatCombination[];
  /** Closest measured alternatives with the change each pin would need (only when unknown). */
  nearest: StatCombination[];
  unknownReason: string | null;
};

const ROLE_ORDER = ["dps", "support"];
const STAT_ORDER = ["atk", "lck", "spd", "dex", "hp", "mp", "def"];

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Flatten one archetype's measured points in wire order. No I/O. */
export function collectStatPoints(
  archetype: PlayerRegionArchetype | null | undefined,
): StatPoint[] {
  const out: StatPoint[] = [];
  if (!archetype) return out;
  for (const region of archetype.regions) {
    for (const point of region.points) {
      out.push({
        archetypeId: archetype.id,
        regionId: region.id ?? null,
        candidateId: point.candidateId ?? null,
        label: point.label ?? null,
        stats: point.stats,
        meanEarned: isFiniteNumber(point.meanEarned) ? point.meanEarned : null,
        partialMean: isFiniteNumber(point.partialMean) ? point.partialMean : null,
        samples: isFiniteNumber(point.samples) ? point.samples : null,
        total: isFiniteNumber(point.total) ? point.total : null,
        unknownCount: isFiniteNumber(point.unknownCount) ? point.unknownCount : null,
        uncertainty: point.uncertainty ?? null,
        evidenceStatus: point.evidenceStatus ?? null,
        classification: point.classification ?? null,
        point,
      });
    }
  }
  return out;
}

/** Split `dps.atk` into role/stat without inventing anything for unusual keys. */
function splitField(field: string): { role: string; stat: string } {
  const index = field.indexOf(".");
  if (index < 0) return { role: field, stat: "" };
  return { role: field.slice(0, index), stat: field.slice(index + 1).toLowerCase() };
}

function orderIndex(list: string[], value: string): number {
  const index = list.indexOf(value);
  return index < 0 ? list.length : index;
}

/**
 * Every stat axis the archetype's own points publish, with the distinct measured stops seen here.
 * Fields with no measured value never appear, so an axis without evidence is simply absent (unknown)
 * rather than given an invented range.
 */
export function statAxes(points: StatPoint[]): StatAxis[] {
  const stopsByField = new Map<string, Set<number>>();
  for (const entry of points) {
    for (const [field, value] of Object.entries(entry.stats)) {
      if (!isFiniteNumber(value)) continue;
      const set = stopsByField.get(field) ?? new Set<number>();
      set.add(value);
      stopsByField.set(field, set);
    }
  }
  const axes: StatAxis[] = [];
  for (const [field, set] of stopsByField) {
    const stops = [...set].sort((left, right) => left - right);
    const { role, stat } = splitField(field);
    axes.push({ field, role, stat, stops, min: stops[0], max: stops[stops.length - 1] });
  }
  axes.sort((left, right) => {
    const roleDiff = orderIndex(ROLE_ORDER, left.role) - orderIndex(ROLE_ORDER, right.role);
    if (roleDiff !== 0) return roleDiff;
    if (left.role !== right.role) return left.role.localeCompare(right.role);
    const statDiff = orderIndex(STAT_ORDER, left.stat) - orderIndex(STAT_ORDER, right.stat);
    if (statDiff !== 0) return statDiff;
    return left.field.localeCompare(right.field);
  });
  return axes;
}

/** The lowest stop OBSERVED on an axis in the current evidence. Never a proven global floor. */
export function axisMinimum(axis: StatAxis): number {
  return axis.min;
}

/** True when the value is one of the discrete measured stops (else the value is unmeasured/unknown). */
export function axisHasStop(axis: StatAxis, value: number): boolean {
  return axis.stops.includes(value);
}

function toCombination(
  point: StatPoint,
  pins: StatPin[],
  axes: StatAxis[],
): StatCombination {
  const pinnedFieldSet = new Set(pins.map((pin) => pin.field));
  const pinnedStats: { field: string; value: number }[] = [];
  const missingFields: string[] = [];
  const deltas: StatPinDelta[] = [];
  let distance = 0;
  for (const pin of pins) {
    const measured = point.stats[pin.field];
    if (isFiniteNumber(measured)) {
      pinnedStats.push({ field: pin.field, value: measured });
      const delta = measured - pin.value;
      deltas.push({ field: pin.field, pinned: pin.value, measured, delta });
      distance += Math.abs(delta);
    } else {
      missingFields.push(pin.field);
      deltas.push({ field: pin.field, pinned: pin.value, measured: null, delta: null });
    }
  }
  const otherStats: { field: string; value: number }[] = [];
  for (const axis of axes) {
    if (pinnedFieldSet.has(axis.field)) continue;
    const value = point.stats[axis.field];
    if (isFiniteNumber(value)) otherStats.push({ field: axis.field, value });
  }
  return {
    archetypeId: point.archetypeId,
    regionId: point.regionId,
    candidateId: point.candidateId,
    label: point.label,
    meanEarned: point.meanEarned,
    partialMean: point.partialMean,
    samples: point.samples,
    total: point.total,
    unknownCount: point.unknownCount,
    uncertainty: point.uncertainty,
    evidenceStatus: point.evidenceStatus,
    classification: point.classification,
    pinnedStats,
    otherStats,
    missingFields,
    deltas,
    distance,
  };
}

function compareNearest(left: StatCombination, right: StatCombination): number {
  if (left.missingFields.length !== right.missingFields.length) {
    return left.missingFields.length - right.missingFields.length;
  }
  if (left.distance !== right.distance) return left.distance - right.distance;
  const leftMean = left.meanEarned ?? Number.NEGATIVE_INFINITY;
  const rightMean = right.meanEarned ?? Number.NEGATIVE_INFINITY;
  return rightMean - leftMean;
}

/**
 * Resolve the current pins against an archetype's own points.
 *
 * - no pins -> `free` (nothing is constrained; the reader has not pinned any axis).
 * - every pin equals a measured value on a single point -> `matched`, and `combinations` lists the
 *   compatible measured points with the other stats they require (the compensation options).
 * - otherwise -> `unknown`: no measured point carries this exact combination. `nearest` lists the
 *   closest measured points with the exact per-field change each pin would need. Values that no
 *   point published are reported as unmeasured; nothing is snapped or interpolated.
 */
export function resolveStatPins(
  points: StatPoint[],
  pins: StatPin[],
  options: { axes?: StatAxis[]; maxNearest?: number } = {},
): StatPinResolution {
  const axes = options.axes ?? statAxes(points);
  const maxNearest = options.maxNearest ?? 3;
  const finitePins = pins.filter((pin) => isFiniteNumber(pin.value));
  if (finitePins.length === 0) {
    return { state: "free", pins: [], axes, combinations: [], nearest: [], unknownReason: null };
  }
  const matches = points.filter((point) =>
    finitePins.every((pin) => point.stats[pin.field] === pin.value),
  );
  if (matches.length > 0) {
    return {
      state: "matched",
      pins: finitePins,
      axes,
      combinations: matches.map((point) => toCombination(point, finitePins, axes)),
      nearest: [],
      unknownReason: null,
    };
  }
  const nearest = points
    .map((point) => toCombination(point, finitePins, axes))
    .sort(compareNearest)
    .slice(0, Math.max(0, maxNearest));
  const unmeasured = finitePins.filter((pin) => {
    const axis = axes.find((entry) => entry.field === pin.field);
    return axis ? !axisHasStop(axis, pin.value) : true;
  });
  const unmeasuredText = unmeasured.map((pin) => `${pin.field} = ${pin.value}`).join(", ");
  const unknownReason =
    unmeasured.length > 0
      ? `No measured point published ${unmeasuredText}. The closest measured alternatives and the stat change each would need are listed; nothing is snapped or interpolated.`
      : `No measured point in this archetype carries this exact combination. The closest measured alternatives and the stat change each would need are listed; the combination is not proven.`;
  return { state: "unknown", pins: finitePins, axes, combinations: [], nearest, unknownReason };
}
