/**
 * Native application of the resident-wide valuables (the five "Water of ..." items).
 *
 * This module adds the *native* half that `resident-stat-items.ts` deliberately does not carry:
 * which parameter each valuable's `bonusType` targets, and whether native raises the parameter's
 * value, its maximum, or both. The item list, the per-item amount and the stat keys stay owned by
 * `resident-stat-items.ts`; this file only derives from them.
 *
 * Recovered from `libil2cpp.so` (fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208):
 *
 *   ecs.ValuableSystem.OnAfterChangedValuableStock 0x160a784
 *     diff = GetSpEffect(type) - GetOldSpEffect(type); if diff != 0 ->
 *   ecs.ValuableSystem.ApplyEffectDifferenceToTargets 0x160a244
 *     type - 7 indexes a 6-entry jump table at VA 0x77213f; entries 0..5 are
 *       0x160a314 (param 10) -> 0x1609f24 ApplyResidentsParamMaxValueUp
 *       0x160a31c (param 14) -> 0x1609c04 ApplyResidentsParamValueUp
 *       0x160a324 (param 12) -> 0x1609f24 ApplyResidentsParamMaxValueUp
 *       0x160a32c (param 13) -> 0x1609c04 ApplyResidentsParamValueUp
 *       0x160a344 (param 11) -> 0x1609f24 ApplyResidentsParamMaxValueUp
 *       0x160a35c (param 10, facility subset) -> 0x16828a4 Parameter.AddMaxValue
 *   with, for each target resident (`ecs.ValuableSystem.GetTargetResidents 0x1609aec` =
 *   `ecs.KingdomSystem.GetResidents(world, includeFriend: true) 0x1599c78` filtered by a predicate):
 *     parameter.Parameter.Add 0x16827e0         value_(+0x14) += n, clamped to maxValue_(+0x18)
 *     parameter.Parameter.AddMaxValue 0x16828a4 maxValue_(+0x18) += n unless it is 0x7fffffff
 *
 * Read side, so the value/max split is observable downstream:
 *   parameter.Parameter.get_value 0x16825cc            = value_ + extraValue_
 *   parameter.Param.GetValue 0x166070c / GetMaxValue 0x16609ec add the equipment contribution.
 *
 * Consequences that are confirmed by that dispatch and must not be guessed around:
 *  * HP(10)/MP(11)/Vigor(12) are raised on the **maximum only** - native never adds the bonus to
 *    the current value of a bounded parameter. Attack(13)/Defence(14) are raised on the **value**.
 *  * `bonusType 12` ("Increase all facilities' Max Durability by 10.") is a facility effect, not a
 *    resident stat, and is applied to a different entity subset with the same param id 10.
 *  * The value/max split is a storage choice only: because every combat read is
 *    `value + extraValue` (see `tools/recovery/combat_parameters.py`), carrying the bonus in
 *    `extraValue`/`extraMax` of the battle-scenario parameter yields the same effective totals as
 *    native writing `value_`/`maxValue_` directly.
 *
 * Master data: `data/sheet-research/raw-copies/KA GameData - Valuable.csv`
 * (`...,explain,bonusCategory,bonusType,bonusMinValue,bonusMaxValue,flag`). Every resident row is
 * `bonusCategory 1`, `bonusType 7..11`, `bonusMinValue == bonusMaxValue == 10`.
 * `tools/valuable-stats-check/run_check.mjs` re-reads that CSV and asserts both the rows and the
 * dispatch below, so a master-data or recovery change fails the check instead of drifting.
 */
import { RESIDENT_STAT_ITEMS, type ResidentStatItemCounts } from "./resident-stat-items";
import { STAT_PARAMETER_IDS, type StatKey } from "./stat-parameter-ids";

/** Which native `parameter.Parameter` mutation a valuable performs. */
export type ResidentValuableOp = "addValue" | "addMaxValue";

export type ResidentValuableEffect = {
  /** `Valuable.csv` `bonusType` for this effect. */
  bonusType: number;
  /** Canonical stat key owned by `resident-stat-items.ts`. */
  stat: StatKey;
  /** Native combat parameter id (`STAT_PARAMETER_IDS`). */
  paramId: number;
  /** Native mutations, in dispatch order. */
  ops: readonly ResidentValuableOp[];
};

/**
 * `bonusType 12`: facility max durability, applied to facilities (param 10 on a facility subset).
 * Recorded so it is not mistaken for a resident stat; it is not part of the resident bonus helper.
 */
export const FACILITY_MAX_DURABILITY_BONUS_TYPE = 12;

/** The five resolved resident valuable effects, bonusType ascending. */
export const RESIDENT_VALUABLE_EFFECTS: readonly ResidentValuableEffect[] = [
  { bonusType: 7, stat: "hp", paramId: STAT_PARAMETER_IDS.hp, ops: ["addMaxValue"] },
  { bonusType: 8, stat: "mp", paramId: STAT_PARAMETER_IDS.mp, ops: ["addMaxValue"] },
  { bonusType: 9, stat: "vig", paramId: STAT_PARAMETER_IDS.vig, ops: ["addMaxValue"] },
  { bonusType: 10, stat: "atk", paramId: STAT_PARAMETER_IDS.atk, ops: ["addValue"] },
  { bonusType: 11, stat: "def", paramId: STAT_PARAMETER_IDS.def, ops: ["addValue"] },
];

const EFFECT_BY_STAT = new Map<StatKey, ResidentValuableEffect>(
  RESIDENT_VALUABLE_EFFECTS.map((effect) => [effect.stat, effect]),
);

/** Resolved native effect for a canonical stat key, or `undefined` when no valuable raises it. */
export function residentValuableEffectForStat(stat: StatKey): ResidentValuableEffect | undefined {
  return EFFECT_BY_STAT.get(stat);
}

function usedCount(counts: ResidentStatItemCounts | undefined | null, key: string): number {
  const raw = counts?.[key];
  return typeof raw === "number" && Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 0;
}

/** Per-parameter native deltas implied by the owned valuables. */
export type ResidentValuableParameterDelta = {
  /** Added to `Parameter.value_` (`rawValue + extraValue`). */
  valueDelta: number;
  /** Added to `Parameter.maxValue_` (`rawMax + extraMax`); a no-op for uncapped parameters. */
  maxDelta: number;
};

/**
 * Owned valuables -> native per-parameter deltas, keyed by native parameter id.
 *
 * Only the ops in `RESIDENT_VALUABLE_EFFECTS` are applied, so a bounded stat (HP/MP/Vigor) yields
 * `valueDelta: 0` exactly as native does, while Attack/Defence land on `valueDelta`. Counts are
 * explicit input: nothing is assumed owned, and a missing/absent entry contributes zero.
 */
export function residentValuableParameterDeltas(
  counts: ResidentStatItemCounts | undefined | null,
): Record<number, ResidentValuableParameterDelta> {
  const out: Record<number, ResidentValuableParameterDelta> = {};
  if (!counts) return out;
  for (const item of RESIDENT_STAT_ITEMS) {
    const effect = EFFECT_BY_STAT.get(item.stat as StatKey);
    if (!effect) continue;
    const total = usedCount(counts, item.key) * item.amount;
    if (total <= 0) continue;
    const entry = (out[effect.paramId] ??= { valueDelta: 0, maxDelta: 0 });
    if (effect.ops.includes("addValue")) entry.valueDelta += total;
    if (effect.ops.includes("addMaxValue")) entry.maxDelta += total;
  }
  return out;
}

/** True when at least one valuable was declared owned. Used to decide "not captured" vs "none". */
export function residentValuablesDeclared(counts: ResidentStatItemCounts | undefined | null): boolean {
  return RESIDENT_STAT_ITEMS.some((item) => usedCount(counts, item.key) > 0);
}
