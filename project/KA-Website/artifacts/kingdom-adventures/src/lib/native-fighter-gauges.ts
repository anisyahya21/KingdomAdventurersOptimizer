import fighterGaugeData from "@/game-data/native-fighter-gauges.json";

/**
 * Recovered native per-fighter HP/MP bars (PASS 13 COMMAND 13.2).
 *
 * The rules below are transcribed from the frozen native code; the data (asset, crops, frames,
 * offsets) comes from `src/game-data/native-fighter-gauges.json`. This module deliberately contains
 * no drawing code and no colours: the stage renders the original `mini_gauge.png` crops, and the
 * team member-info panel (`battle/member_info_bar.*`) and the `GaugeSystem` world gauges are
 * different systems that are never used here.
 */

export const FIGHTER_GAUGES = fighterGaugeData as unknown as {
  note: string;
  nativePath: string;
  resource: { manager: string; sebId: number; sebFile: string; imageId: number; imageFile: string };
  asset: { path: string; width: number; height: number; sha256: string; source: string };
  records: {
    background: { u: number; v: number; w: number; h: number; transX: number; transY: number };
    fill: {
      u: number;
      w: number;
      h: number;
      transX: number;
      transY: number;
      rowByFrame: Record<string, number>;
    };
  };
  meters: Record<"hp" | "mp" | "readiness", { paramId?: number; frame: number; fillRowV: number; implemented?: boolean }>;
  geometry: {
    drawGaugesXOffset: number;
    drawGaugesYFromScreen: number;
    hpYOffset: number;
    mpYOffset: number;
  };
  rate: { rule: string };
  fill: { rule: string };
  visibility: { rule: string; replayEquivalent: string };
};

export type FighterGaugeKind = "hp" | "mp";

/** Native `Param.GetRate` 0x165E91C: the displayed rate in whole percent, 0..100. */
export function fighterGaugeRate(current: number, max: number): number {
  if (!Number.isFinite(current) || !Number.isFinite(max)) return 0;
  if (max <= 0) return 0;
  const scaled = Math.trunc(current) * 100;
  // signed truncating division, exactly as the native sdiv + Clamp(...,0,100) sequence
  const rate = Math.max(0, Math.min(100, Math.trunc(scaled / Math.trunc(max))));
  if (rate === 0 && Math.trunc(current) > 0) return 1; // native minimum-visible rate
  return rate;
}

/**
 * Native `AppData.DrawHorizontalMeter` fill width for one meter: `rate < 1` draws nothing,
 * otherwise `(w * rate > 99) ? trunc(w * rate / 100) : 1` (the native `csinc` guarantees one pixel
 * for any visible rate). `width` is the fill record's own source width (15 for `mini_gauge`).
 */
export function fighterGaugeFillWidth(width: number, rate: number): number {
  if (!Number.isFinite(rate) || rate < 1) return 0;
  const product = Math.trunc(width) * Math.trunc(rate);
  if (product <= 99) return 1;
  return Math.trunc(product / 100);
}

export type FighterGaugeLayout = {
  /** outer meter rect in logical scene px, relative to the unit's native screen position */
  x: number;
  y: number;
  width: number;
  height: number;
  /** fill rect inside the meter, relative to the meter origin */
  fill: { x: number; y: number; width: number; maxWidth: number; height: number };
  /** source crops of the original asset */
  backgroundCrop: { u: number; v: number; w: number; h: number };
  fillCrop: { u: number; v: number; w: number; h: number };
  frame: number;
  rate: number;
};

/**
 * Meter geometry for one fighter and one bar kind. `screenX`/`screenY` are the unit's native logical
 * screen position - the same point the replay already uses as the unit origin - so `DrawDynamic`'s
 * `(screenX, screenY - 5)` and `DrawGauges`' `-9` / `+1` / `+2` produce
 * `(screenX - 9, screenY - 4)` for HP and `(screenX - 9, screenY - 3)` for MP.
 */
export function fighterGaugeLayout(
  kind: FighterGaugeKind,
  screenX: number,
  screenY: number,
  current: number,
  max: number,
): FighterGaugeLayout {
  const geometry = FIGHTER_GAUGES.geometry;
  const background = FIGHTER_GAUGES.records.background;
  const fillRecord = FIGHTER_GAUGES.records.fill;
  const frame = FIGHTER_GAUGES.meters[kind].frame;
  const rate = fighterGaugeRate(current, max);
  const fillWidth = fighterGaugeFillWidth(fillRecord.w, rate);
  return {
    x: screenX + geometry.drawGaugesXOffset,
    y: screenY + geometry.drawGaugesYFromScreen + (kind === "hp" ? geometry.hpYOffset : geometry.mpYOffset),
    width: background.w,
    height: background.h,
    fill: {
      x: fillRecord.transX,
      y: fillRecord.transY,
      width: fillWidth,
      maxWidth: fillRecord.w,
      height: fillRecord.h,
    },
    backgroundCrop: { u: background.u, v: background.v, w: background.w, h: background.h },
    fillCrop: { u: fillRecord.u, v: fillRecord.rowByFrame[String(frame)], w: fillRecord.w, h: fillRecord.h },
    frame,
    rate,
  };
}

/**
 * Native `FighterSystem.IsMostFront` 0x15884f8 = `(grid + 4) < 9` with `grid = rowIndex * 5 + column`
 * for the unit's *own* team, i.e. `rowIndex === 0`: allies stand on `rowOffset + 1`, opponents on
 * `rowOffset`. This is the same predicate the replay's occupancy model already uses as its
 * advancement gate, reused here rather than re-derived.
 */
export function isMostFrontUnit(
  side: "ally" | "enemy",
  cellRow: number,
  rowOffset: number,
): boolean {
  return side === "ally" ? cellRow === rowOffset + 1 : cellRow === rowOffset;
}

export type FighterVitals = {
  hp: { current: number; max: number; source: string };
  mp: { current: number; max: number; source: string };
};

/**
 * Live per-fighter vitals for the replay state.
 *
 * The replay state model tracks cells and alive/KO per beat but carries no HP/MP fields (the
 * encounter timeline gives damage and heal amounts only, and no MP spend at all), so the vitals are
 * derived from the replay state itself instead of being invented:
 *
 *  * `max` HP = the total damage the timeline applies to that unit, floored at 1, so a unit that is
 *    never damaged still has a full bar and a KO'd unit lands exactly on 0;
 *  * `current` HP = `max` minus the damage applied before the current beat, plus heals, floored at 0;
 *  * MP = full for every unit, because the replay state contains no MP spending. The MP bar is
 *    therefore drawn by the same native rule with rate 100; when the simulator exposes real MP
 *    values, `replayFighterVitals` is the single place to feed them in.
 */
export function replayFighterVitals(
  timeline: { side: "ally" | "enemy"; targetSide: "ally" | "enemy"; target: number; kind: string; amount?: number }[],
  side: "ally" | "enemy",
  index: number,
  step: number,
): FighterVitals {
  const upTo = Math.max(0, Math.min(step, timeline.length));
  let maxHp = 0;
  let current = 0;
  for (let beat = 0; beat < timeline.length; beat += 1) {
    const event = timeline[beat];
    if (event.targetSide !== side || event.target !== index) continue;
    const amount = typeof event.amount === "number" ? event.amount : 0;
    if (event.kind === "heal") {
      if (beat < upTo) current += amount;
      continue;
    }
    if (event.kind === "attack" || event.kind === "critical" || event.kind === "weak") {
      maxHp += amount;
      if (beat < upTo) current -= amount;
    }
  }
  const hpMax = Math.max(1, maxHp);
  const hpCurrent = Math.max(0, Math.min(hpMax, hpMax + current));
  return {
    hp: { current: hpCurrent, max: hpMax, source: "replay timeline damage/heal at the current beat" },
    mp: { current: 1, max: 1, source: "replay state carries no MP spend; MP reads full" },
  };
}
