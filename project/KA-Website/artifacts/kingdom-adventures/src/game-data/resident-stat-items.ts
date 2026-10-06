/**
 * Resident-wide stat items: the five "Water of ..." valuables.
 *
 * Source: the original Valuable table shipped with the game,
 * `KA_assets/xls/<lang>.lproj/Valuable.txt` (ids 0..99). Every row of a given
 * water carries the same effect text, for example:
 *
 *   命の聖水   / Water of Life        "Increases all residents' HP by 10."
 *   知の聖水   / Water of Wisdom      "Increases all residents' MP by 10."
 *   活力の聖水 / Water of Vitality    "Increases all residents' Vigor by 10."
 *   力の聖水   / Water of Might       "Increases all residents' Attack by 10."
 *   守の聖水   / Water of Resilience  "Increases all residents' Defence by 10."
 *
 * Each water is a one-shot consumable that raises that one stat for *every*
 * resident by the same fixed amount, so the total bonus is
 * (number used) x (amount). The table has no equivalent item for Speed, Luck,
 * Intelligence, Dexterity, Gather, Move or Heart - those stats only ever come
 * from the job curve and equipment.
 *
 * Native applies the bonus after character creation, in `ecs.ValuableSystem` (see
 * `resident-valuable-effects.ts` for the recovered dispatch): HP/MP/Vigor raise the parameter's
 * *maximum* through `Parameter.AddMaxValue 0x16828a4`, Attack/Defence raise its *value* through
 * `Parameter.Add 0x16827e0`, and the combat read is `value_ + extraValue_`
 * (`Parameter.get_value 0x16825cc`). The loadout conversion therefore carries the bonus as the
 * parameter's extra value/maximum, which is why a resident's effective stat can sit above the job
 * curve at the same level. It is a player-wide bonus, not a per-job value.
 */

export type ResidentStatItem = {
  key: string;
  name: string;
  /** Canonical stat key used by the stat tables. */
  stat: string;
  /** Flat bonus added per item used. */
  amount: number;
};

export const RESIDENT_STAT_ITEM_AMOUNT = 10;

export const RESIDENT_STAT_ITEMS: ResidentStatItem[] = [
  { key: "life", name: "Water of Life", stat: "hp", amount: RESIDENT_STAT_ITEM_AMOUNT },
  { key: "wisdom", name: "Water of Wisdom", stat: "mp", amount: RESIDENT_STAT_ITEM_AMOUNT },
  { key: "vitality", name: "Water of Vitality", stat: "vig", amount: RESIDENT_STAT_ITEM_AMOUNT },
  { key: "might", name: "Water of Might", stat: "atk", amount: RESIDENT_STAT_ITEM_AMOUNT },
  { key: "resilience", name: "Water of Resilience", stat: "def", amount: RESIDENT_STAT_ITEM_AMOUNT },
];

/** How many of each water the player has used, keyed by `ResidentStatItem.key`. */
export type ResidentStatItemCounts = Record<string, number | undefined>;

function usedCount(counts: ResidentStatItemCounts | undefined | null, key: string): number {
  const raw = counts?.[key];
  return typeof raw === "number" && Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 0;
}

/** Counts -> flat bonus per canonical stat key. */
export function residentStatItemBonuses(counts: ResidentStatItemCounts | undefined | null): Record<string, number> {
  const out: Record<string, number> = {};
  if (!counts) return out;
  for (const item of RESIDENT_STAT_ITEMS) {
    const total = usedCount(counts, item.key) * item.amount;
    if (total > 0) out[item.stat] = (out[item.stat] ?? 0) + total;
  }
  return out;
}

/** Flat bonus for a single water, e.g. 4 x Water of Life = 40. */
export function residentStatItemTotal(item: ResidentStatItem, counts: ResidentStatItemCounts | undefined | null): number {
  return usedCount(counts, item.key) * item.amount;
}
